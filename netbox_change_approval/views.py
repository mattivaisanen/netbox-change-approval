from django.contrib import messages
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.generic import View

from netbox.context import current_request
from netbox.object_actions import BulkExport
from netbox.views import generic
from utilities.forms.forms import ConfirmationForm
from utilities.views import ConditionalLoginRequiredMixin, register_model_view

from . import absence, amend, approvals, changesets, components, filtersets, forms, inbox, notifications, permissions, placement, revision, serialization, tables
from .exceptions import AlreadyDecided, ChangeConflict, CreatedObjectInUse
from .rule_access import RuleReadMixin, RuleWriteMixin
from .object_actions import BulkApprove, BulkReject
from .models import ActionChoices, ApprovalRule, ChangeRequest, ChangeRequestComment, StatusChoices

#
# Approval rules (the one rule table)
#


@register_model_view(ApprovalRule, 'list', path='', detail=False)
class ApprovalRuleListView(RuleReadMixin, generic.ObjectListView):
    queryset = ApprovalRule.objects.all()
    filterset = filtersets.ApprovalRuleFilterSet
    filterset_form = forms.ApprovalRuleFilterForm
    table = tables.ApprovalRuleTable


@register_model_view(ApprovalRule)
class ApprovalRuleView(RuleReadMixin, generic.ObjectView):
    queryset = ApprovalRule.objects.all()
    template_name = 'netbox_change_approval/approvalrule.html'


@register_model_view(ApprovalRule, 'add', detail=False)
@register_model_view(ApprovalRule, 'edit')
class ApprovalRuleEditView(RuleWriteMixin, generic.ObjectEditView):
    queryset = ApprovalRule.objects.all()
    form = forms.ApprovalRuleForm


@register_model_view(ApprovalRule, 'delete')
class ApprovalRuleDeleteView(RuleWriteMixin, generic.ObjectDeleteView):
    queryset = ApprovalRule.objects.all()


@register_model_view(ApprovalRule, 'bulk_delete', path='delete', detail=False)
class ApprovalRuleBulkDeleteView(RuleWriteMixin, generic.BulkDeleteView):
    queryset = ApprovalRule.objects.all()
    filterset = filtersets.ApprovalRuleFilterSet
    table = tables.ApprovalRuleTable


#
# Change requests
#


class _AnyLoggedInUserCanView:
    """Change requests are readable by every logged-in user, without an admin
    having to grant NetBox's "view change request" permission first. Who can
    approve, reject or retract is still checked in those views."""
    def has_permission(self):
        return self.request.user.is_authenticated


_can_decide = permissions.can_decide


def _relation_for_key(model, key):
    field_name = key[:-3] if key.endswith('_id') else key
    try:
        field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return None
    if not field.is_relation or field.related_model is None:
        return None
    return field


def prefetch_display_names(change_requests):
    """
    {(related model, pk): str} for every related object named in these
    requests' diffs, fetched with one query per related model. _diff_rows()
    uses it instead of one query per field per request (the inbox and the
    dashboard widget show many requests at once).
    """
    wanted = {}
    for change_request in change_requests:
        diff = change_request.diff
        model = change_request.content_type.model_class()
        if model is None or not isinstance(diff, dict):
            continue
        for key, value in diff.items():
            field = _relation_for_key(model, key)
            if field is None:
                continue
            values = (
                [value.get('old'), value.get('new')]
                if change_request.action == ActionChoices.UPDATE and isinstance(value, dict) else [value]
            )
            pks = wanted.setdefault(field.related_model, set())
            for item in values:
                for pk in (item if isinstance(item, (list, tuple)) else [item]):
                    if isinstance(pk, (int, str)) and not isinstance(pk, bool):
                        pks.add(pk)
    names = {}
    for related_model, pks in wanted.items():
        try:
            for obj in related_model.objects.filter(pk__in=pks):
                names[(related_model, obj.pk)] = str(obj)
        except (ValueError, TypeError):
            continue
    return names


def _resolve_display(model, key, value, names=None):
    """
    Diff values are stored as raw PKs for FK/M2M fields (that's what apply()
    needs to reconstruct the object) — but a bare integer tells an approver
    nothing about *which* site, rack, location, etc. is involved. Resolve to the
    related object's own string representation for display wherever possible.
    """
    if value is None:
        return value

    field_name = key[:-3] if key.endswith('_id') else key
    try:
        field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return value

    if not field.is_relation or field.related_model is None:
        return value

    related_model = field.related_model
    if names is not None:
        if field.many_to_many:
            if not isinstance(value, (list, tuple)):
                return value
            return [names.get((related_model, pk), f'#{pk} (not found)') for pk in value]
        if (related_model, value) in names:
            return names[(related_model, value)]
    if field.many_to_many:
        if not isinstance(value, (list, tuple)):
            return value
        found = {obj.pk: str(obj) for obj in related_model.objects.filter(pk__in=value)}
        return [found.get(pk, f'#{pk} (not found)') for pk in value]

    try:
        return str(related_model.objects.get(pk=value))
    except related_model.DoesNotExist:
        return f'#{value} (not found)'


def _field_label(model, key):
    """Diff keys are attnames (e.g. 'device_id', 'u_height') — the raw storage
    name, not something an approver should have to decode. Resolve to the
    field's own verbose_name, same label NetBox's own forms show."""
    field_name = key[:-3] if key.endswith('_id') else key
    try:
        field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return key
    return str(field.verbose_name)


def _tidy(value):
    """Show whole numbers stored as decimals (rack position 10.0) as 10."""
    from decimal import Decimal

    if isinstance(value, bool):
        return value
    if isinstance(value, (Decimal, float)) and value == int(value):
        return int(value)
    if isinstance(value, str):
        try:
            number = Decimal(value)
        except Exception:
            return value
        if number == number.to_integral_value() and '.' in value and value.replace('.', '', 1).lstrip('-').isdigit():
            return str(int(number))
    return value


def _diff_rows(change_request, conflict_fields=(), names=None):
    rows = _raw_diff_rows(change_request, conflict_fields, names)
    for row in rows:
        row['old'] = _tidy(row['old'])
        row['new'] = _tidy(row['new'])
    return rows


def _raw_diff_rows(change_request, conflict_fields=(), names=None):
    diff = change_request.diff
    if not isinstance(diff, dict):
        return []
    model = change_request.content_type.model_class()

    if change_request.action == ActionChoices.UPDATE:
        return [
            {
                'field': field,
                'label': _field_label(model, field),
                'old': _resolve_display(model, field, change.get('old'), names),
                'new': _resolve_display(model, field, change.get('new'), names),
                'conflict': field in conflict_fields,
            }
            for field, change in sorted(diff.items())
        ]
    if change_request.action == ActionChoices.CREATE:
        return [
            {'field': field, 'label': _field_label(model, field), 'old': None, 'new': _resolve_display(model, field, value, names)}
            for field, value in sorted(diff.items())
        ]
    # delete: diff is a full snapshot of what's being removed
    return [
        {'field': field, 'label': _field_label(model, field), 'old': _resolve_display(model, field, value, names), 'new': _('(deleted)')}
        for field, value in sorted(diff.items())
    ]


@register_model_view(ChangeRequest, 'list', path='', detail=False)
class ChangeRequestListView(_AnyLoggedInUserCanView, generic.ObjectListView):
    queryset = ChangeRequest.objects.all()
    filterset = filtersets.ChangeRequestFilterSet
    filterset_form = forms.ChangeRequestFilterForm
    table = tables.ChangeRequestTable

    def get_queryset(self, request):
        # Requests about object types you can view, plus your own and your
        # approver groups'. See permissions.visible_change_requests.
        return permissions.visible_change_requests(request.user, super().get_queryset(request))


@register_model_view(ChangeRequest, 'all', path='all', detail=False)
class AllChangeRequestsView(ChangeRequestListView):
    """The same list under its own address, for the menu. NetBox highlights a
    menu item whose address is the start of the current page's address, so
    linking the menu to change-requests/ lit "All Change Requests" up on the
    inbox and Approvals pages too (both live under change-requests/)."""


@register_model_view(ChangeRequest, 'mine', path='mine', detail=False)
class MyChangeRequestsView(View):
    """Old "My Requests" page, replaced by the Approval Inbox. Kept as a
    redirect so bookmarks and old links still work."""
    def get(self, request):
        return redirect(reverse('plugins:netbox_change_approval:changerequest_inbox'))


@register_model_view(ChangeRequest, 'approvals', path='approvals', detail=False)
class ApprovalsQueueView(_AnyLoggedInUserCanView, generic.ObjectListView):
    """"Approvals" nav tab: the actionable queue — pending requests routed to a
    group this user is in (every pending request, for a superuser)."""
    queryset = ChangeRequest.objects.filter(status=StatusChoices.PENDING)
    filterset = filtersets.ChangeRequestFilterSet
    filterset_form = forms.ChangeRequestFilterForm
    table = tables.ChangeRequestTable
    template_name = 'netbox_change_approval/changerequest_approvals.html'
    # Tick requests, then Approve/Reject Selected. Export kept; no add/import/
    # bulk edit/delete (requests come from edits elsewhere).
    actions = (BulkExport, BulkApprove, BulkReject)

    def get_queryset(self, request):
        return permissions.approver_queue(request.user, super().get_queryset(request))

    def get_extra_context(self, request):
        # Replaces the old top-right navbar ticker (see template_content.py) —
        # self.queryset is already this user's scoped, still-pending set at this
        # point (BaseMultiObjectView.dispatch() sets it before extra context is
        # built), so counting it is exactly what the old ticker counted.
        return {'pending_count': self.queryset.count()}


@register_model_view(ChangeRequest)
class ChangeRequestView(_AnyLoggedInUserCanView, generic.ObjectView):
    queryset = ChangeRequest.objects.all()

    # A request shows the object's field values, so you can open it if you
    # can view that object in NetBox (or submitted it / are its approver).
    # See permissions.can_view_change_request.

    def get(self, request, **kwargs):
        change_request = ChangeRequest.objects.filter(pk=kwargs.get('pk')).first()
        if change_request is not None and not permissions.can_view_change_request(request.user, change_request):
            messages.warning(
                request,
                _("You don't have access to this change request, because you can't view the object it changes."),
            )
            return redirect(reverse('plugins:netbox_change_approval:changerequest_inbox'))
        if change_request is not None and request.user.is_authenticated:
            # Opening your own rejected request takes it out of your inbox count.
            inbox.mark_rejections_seen(request.user, ChangeRequest.objects.filter(pk=change_request.pk))
        return super().get(request, **kwargs)

    def get_extra_context(self, request, instance):
        batch_siblings = ChangeRequest.objects.none()
        if instance.batch_id:
            batch_siblings = ChangeRequest.objects.filter(
                batch_id=instance.batch_id,
            ).exclude(pk=instance.pk)
        conflict_fields, object_missing = [], False
        if instance.status == StatusChoices.PENDING and instance.action == ActionChoices.UPDATE:
            conflict_fields, object_missing = serialization.conflicts_for(
                instance.content_type, instance.object_id, instance.diff,
            )
        elif instance.status == StatusChoices.PENDING and instance.object_id is not None:
            # A pending create or delete whose object was removed since.
            model = instance.content_type.model_class()
            object_missing = model is not None and not model.objects.filter(pk=instance.object_id).exists()
        return {
            'diff_rows': _diff_rows(instance, conflict_fields),
            'conflict_labels': [
                _field_label(instance.content_type.model_class(), f) for f in conflict_fields
            ],
            'object_missing': object_missing,
            'placement_rows': placement.placement_rows(instance),
            'rack_previews': placement.rack_previews(instance),
            'change_set': changesets.for_request(instance),
            # Unchanged placement (site › rack › ...) for the location line;
            # changed placement fields are rows of the change table already.
            'where': [
                row for row in placement.placement_rows(instance)
                if not row.get('changed') and row.get('value') is not None
                and hasattr(row.get('value'), 'get_absolute_url')
            ],
            'batch_siblings': batch_siblings,
            'can_decide': _can_decide(request.user, instance),
            'has_approved': instance.has_approved(request.user),
            'approvals_so_far': approvals.approvers_so_far(instance),
            'comments': instance.comments.select_related('author'),
            'comment_form': forms.ChangeRequestCommentForm(),
            'components': components.component_summary(instance),
            'update_url': (
                amend.edit_url(instance) if amend.amendable(instance, request.user) else None
            ),
            'can_retract': (
                instance.status == StatusChoices.PENDING and instance.submitted_by_id == request.user.pk
            ),
            'can_revise': (
                instance.status == StatusChoices.RETURNED and instance.submitted_by_id == request.user.pk
            ),
            'revise_url': (
                revision.revise_url(instance) if instance.status == StatusChoices.RETURNED else None
            ),
        }


def _batch_targets(change_request, user=None):
    """
    The pending requests decided together with `change_request`: the rest of
    its bulk batch, limited to those `user` may act on. A bulk edit can touch
    objects routed to different approver groups (one rule per tenant, say);
    approving the batch must not decide requests meant for another group.
    The submitter (retracting) gets their own requests in the batch.

    `user` defaults to the user of the current web request.
    """
    if not change_request.batch_id:
        return [change_request]
    if user is None:
        user = getattr(current_request.get(), 'user', None)
    siblings = ChangeRequest.objects.filter(batch_id=change_request.batch_id, status=StatusChoices.PENDING)
    if user is None:
        return list(siblings)
    if change_request.submitted_by_id == user.pk:
        return list(siblings.filter(submitted_by=user))
    return [
        cr for cr in siblings
        if cr.pk == change_request.pk or permissions.can_decide(user, cr)
    ]


def _close_all(request, targets, method, *args):
    """
    Call `method` (reject, send_back, retract) on every request in `targets`
    (one request, or a whole batch), all or nothing. If any of them was
    decided by someone else in the meantime, nothing is changed and the user
    is told; returns False then.
    """
    from django.db import transaction

    try:
        with transaction.atomic():
            for change_request in removal_order(targets):
                getattr(change_request, method)(request.user, *args)
    except AlreadyDecided as e:
        messages.warning(request, _('Nothing changed: %(error)s') % {'error': e})
        return False
    except CreatedObjectInUse as e:
        messages.error(request, _('Nothing changed: %(error)s') % {'error': e})
        return False
    return True


def removal_order(targets):
    """Requests in the order their new objects can be removed: newest first,
    so a device created in a new rack goes before the rack."""
    return sorted(targets, key=lambda cr: cr.pk or 0, reverse=True)


def _default_return_url():
    # Where a decision lands when nothing more specific was requested (e.g.
    # decided straight from the detail page) — the queue, not the just-decided
    # request's own detail page, so approvers keep moving through the list
    # instead of bouncing back to a dead end each time.
    return reverse('plugins:netbox_change_approval:changerequest_list')


def _safe_return_url(url, fallback=None):
    """`url` if it is a path on this site, else `fallback` (default: the
    request list). return_url comes from the query string or a hidden form
    field, so without this a crafted link could send an approver to another
    site after deciding, or run a javascript: URL from the Cancel button."""
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts=None):
        return url
    return fallback or _default_return_url()


class _ChangeRequestDecisionView(ConditionalLoginRequiredMixin, View):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.change_request = get_object_or_404(ChangeRequest, pk=kwargs['pk'])

    def _not_open(self, request):
        """Already decided or withdrawn (perhaps by someone else a moment
        ago): say so instead of a bare 404."""
        if self.change_request.status == StatusChoices.PENDING:
            return None
        messages.warning(request, _('Request #%(pk)s is already %(status)s; nothing changed.') % {
            'pk': self.change_request.pk, 'status': self.change_request.get_status_display().lower(),
        })
        return redirect(self.change_request.get_absolute_url())

    def check_permission(self, request):
        if (closed := self._not_open(request)) is not None:
            return closed
        if self.change_request.submitted_by_id == request.user.pk:
            messages.error(request, _('You cannot decide your own request. Another approver has to.'))
            return redirect(self.change_request.get_absolute_url())
        if not _can_decide(request.user, self.change_request):
            messages.error(request, _("You are not a member of this request's approver group."))
            return redirect(self.change_request.get_absolute_url())
        return None


@register_model_view(ChangeRequest, 'approve')
class ChangeRequestApproveView(_ChangeRequestDecisionView):
    def check_permission(self, request):
        if (denied := super().check_permission(request)) is not None:
            return denied
        if self.change_request.has_approved(request.user):
            messages.warning(request, _('You already approved this. It needs another approver.'))
            return redirect(self.change_request.get_absolute_url())
        return None

    def get(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        targets = _batch_targets(self.change_request)
        return render(request, 'netbox_change_approval/changerequest_approve.html', {
            'object': self.change_request,
            'form': ConfirmationForm(initial=request.GET),
            'return_url': _safe_return_url(request.GET.get('return_url')),
            'button_class': 'success',
            'batch_count': len(targets),
        })

    def post(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        form = ConfirmationForm(request.POST)
        if not form.is_valid():
            messages.error(request, _('Could not process request.'))
            return redirect(_default_return_url())

        targets = _batch_targets(self.change_request)
        try:
            # All-or-nothing for a batch: one conflict or validation failure
            # rolls back every change already applied in this batch (and the
            # approvals just recorded), and every request stays pending.
            result = approvals.approve(targets, request.user)
        except ChangeConflict as e:
            messages.error(request, _('Not applied: %(error)s Reject this request and resubmit it.') % {'error': e})
            return redirect(self.change_request.get_absolute_url())
        except (ValidationError, FieldDoesNotExist) as e:
            messages.error(request, _('Not applied: %(error)s') % {'error': '; '.join(getattr(e, 'messages', [str(e)]))})
            return redirect(self.change_request.get_absolute_url())
        except approvals.AlreadyDecided as e:
            messages.warning(request, str(e))
            return redirect(self.change_request.get_absolute_url())
        if result.applied:
            messages.success(request, _('Approved and applied %(count)d change(s).') % {'count': len(targets)})
        else:
            messages.success(request, _(
                'Approval recorded (%(have)d of %(need)d). It is applied once %(left)d more approver(s) approve.'
            ) % {'have': result.have, 'need': result.need, 'left': result.need - result.have})
        return redirect(_safe_return_url(form.cleaned_data.get('return_url')))


@register_model_view(ChangeRequest, 'reject')
class ChangeRequestRejectView(_ChangeRequestDecisionView):
    def get(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        targets = _batch_targets(self.change_request)
        return render(request, 'netbox_change_approval/changerequest_reject.html', {
            'object': self.change_request,
            'form': forms.ChangeRequestRejectForm(initial=request.GET),
            'return_url': _safe_return_url(request.GET.get('return_url')),
            'batch_count': len(targets),
        })

    def post(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        form = forms.ChangeRequestRejectForm(request.POST)
        if not form.is_valid():
            return render(request, 'netbox_change_approval/changerequest_reject.html', {
                'object': self.change_request,
                'form': form,
                'return_url': _safe_return_url(request.POST.get('return_url')),
                'batch_count': len(_batch_targets(self.change_request)),
            })

        targets = _batch_targets(self.change_request)
        if not _close_all(request, targets, 'reject', form.cleaned_data['comment']):
            return redirect(self.change_request.get_absolute_url())
        messages.success(request, _('Rejected %(count)d change(s).') % {'count': len(targets)})
        return redirect(_safe_return_url(form.cleaned_data.get('return_url')))


@register_model_view(ChangeRequest, 'retract')
class ChangeRequestRetractView(_ChangeRequestDecisionView):
    """
    Lets the original submitter withdraw their own still-pending request — a
    different permission check from approve/reject (submitter, not approver
    group member), so this overrides check_permission() rather than reusing
    _can_decide(). No comment field: unlike a rejection, there's no one else
    to explain the decision to.
    """
    def check_permission(self, request):
        if (closed := self._not_open(request)) is not None:
            return closed
        if self.change_request.submitted_by_id != request.user.pk:
            messages.error(request, _('You can only retract your own requests.'))
            return redirect(self.change_request.get_absolute_url())
        return None

    def get(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        targets = _batch_targets(self.change_request)
        return render(request, 'netbox_change_approval/changerequest_retract.html', {
            'object': self.change_request,
            'form': ConfirmationForm(initial=request.GET),
            'return_url': _safe_return_url(request.GET.get('return_url')),
            'batch_count': len(targets),
        })

    def post(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        form = ConfirmationForm(request.POST)
        if not form.is_valid():
            messages.error(request, _('Could not process request.'))
            return redirect(_default_return_url())

        targets = _batch_targets(self.change_request)
        if request.POST.get('then') == 'edit':
            # Same as an approver sending it back, without a note: the
            # submitter lands on the prefilled form and saving it resubmits
            # this same request (see revision.py).
            if not _close_all(request, targets, 'send_back', ''):
                return redirect(self.change_request.get_absolute_url())
            return redirect(reverse(
                'plugins:netbox_change_approval:changerequest_revise', kwargs={'pk': self.change_request.pk},
            ))
        if not _close_all(request, targets, 'retract'):
            return redirect(self.change_request.get_absolute_url())
        messages.success(request, _('Retracted %(count)d change(s).') % {'count': len(targets)})
        return redirect(_safe_return_url(form.cleaned_data.get('return_url')))


@register_model_view(ChangeRequest, 'sendback', path='send-back')
class ChangeRequestSendBackView(_ChangeRequestDecisionView):
    """Approver asks the submitter to revise. Like reject (comment required,
    whole batch), but the submitter can resubmit the same request."""
    template_name = 'netbox_change_approval/changerequest_sendback.html'

    def _render(self, request, form, return_url):
        return render(request, self.template_name, {
            'object': self.change_request,
            'form': form,
            'return_url': return_url,
            'button_class': 'warning',
            'batch_count': len(_batch_targets(self.change_request)),
        })

    def get(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        return self._render(
            request, forms.ChangeRequestSendBackForm(initial=request.GET),
            _safe_return_url(request.GET.get('return_url')),
        )

    def post(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        form = forms.ChangeRequestSendBackForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, _safe_return_url(request.POST.get('return_url')))

        targets = _batch_targets(self.change_request)
        if not _close_all(request, targets, 'send_back', form.cleaned_data['comment']):
            return redirect(self.change_request.get_absolute_url())
        messages.success(
            request,
            _('Sent %(count)d change(s) back to %(user)s for revision.') % {
                'count': len(targets), 'user': self.change_request.submitted_by or _('the submitter'),
            },
        )
        return redirect(_safe_return_url(form.cleaned_data.get('return_url')))


class _SentBackView(ConditionalLoginRequiredMixin, View):
    """Base for the submitter's two choices on a sent-back request."""
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.change_request = get_object_or_404(
            ChangeRequest.objects.filter(status=StatusChoices.RETURNED), pk=kwargs['pk'],
        )

    def check_permission(self, request):
        if self.change_request.submitted_by_id != request.user.pk:
            messages.error(request, _('Only the submitter can revise or discard this request.'))
            return redirect(self.change_request.get_absolute_url())
        return None


@register_model_view(ChangeRequest, 'revise')
class ChangeRequestReviseView(_SentBackView):
    """Opens NetBox's own add/edit/delete page for the object, prefilled with
    what was submitted. Saving it resubmits this same request (revision.py)."""
    def get(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        url = revision.revise_url(self.change_request)
        if url is None:
            messages.error(request, _("The object this request changes no longer exists. Discard the request instead."))
            return redirect(self.change_request.get_absolute_url())
        if self.change_request.decision_comment:
            messages.info(request, revision.revise_message(self.change_request))
        return redirect(url)


@register_model_view(ChangeRequest, 'discard')
class ChangeRequestDiscardView(_SentBackView):
    def get(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        return render(request, 'netbox_change_approval/changerequest_discard.html', {
            'object': self.change_request,
            'form': ConfirmationForm(initial=request.GET),
            'return_url': _safe_return_url(request.GET.get('return_url'), self.change_request.get_absolute_url()),
        })

    def post(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        form = ConfirmationForm(request.POST)
        if not form.is_valid():
            messages.error(request, _('Could not process request.'))
            return redirect(self.change_request.get_absolute_url())
        targets = [self.change_request]
        if self.change_request.batch_id:
            targets = list(ChangeRequest.objects.filter(
                batch_id=self.change_request.batch_id, status=StatusChoices.RETURNED,
                submitted_by_id=request.user.pk,
            ))
        if not _close_all(request, targets, 'retract'):
            return redirect(self.change_request.get_absolute_url())
        messages.success(request, _('Discarded %(count)d change(s).') % {'count': len(targets)})
        return redirect(reverse('plugins:netbox_change_approval:changerequest_inbox'))


@register_model_view(ChangeRequest, 'comment')
class ChangeRequestCommentView(ConditionalLoginRequiredMixin, View):
    """Add a comment to a request: ask or answer something without deciding
    it. Anyone who can open the request can comment. The submitter, the
    approver group and earlier commenters are notified."""
    def post(self, request, pk):
        change_request = get_object_or_404(ChangeRequest, pk=pk)
        if not request.user.is_authenticated or not permissions.can_view_change_request(request.user, change_request):
            messages.error(request, _("You don't have access to this change request."))
            return redirect(reverse('plugins:netbox_change_approval:changerequest_inbox'))
        form = forms.ChangeRequestCommentForm(request.POST)
        if form.is_valid():
            comment = ChangeRequestComment.objects.create(
                change_request=change_request, author=request.user, text=form.cleaned_data['text'],
            )
            notifications.comment_added(comment)
            messages.success(request, _('Comment added.'))
        else:
            messages.error(request, _('Comment not added: %(errors)s') % {
                'errors': '; '.join(e for errors in form.errors.values() for e in errors),
            })
        return redirect(f'{change_request.get_absolute_url()}#comments')


def _with_param(request, name, value):
    """This page's query string with `name` set to `value`."""
    params = request.GET.copy()
    params[name] = str(value)
    return params.urlencode()


@register_model_view(ChangeRequest, 'inbox', path='inbox', detail=False)
class InboxView(ConditionalLoginRequiredMixin, View):
    """The Approval Inbox (see inbox.py), linked from the navbar button."""
    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        user = request.user
        sections = []
        for key, title, queryset, counted in inbox.inbox_sections(user):
            total = inbox.item_count(queryset)
            pages = inbox.page_count(total)
            try:
                page = min(max(1, int(request.GET.get(f'{key}_page', 1))), pages)
            except (TypeError, ValueError):
                page = 1
            items = inbox.group_items(queryset, page=page)
            members = [member for item in items for member in item['members']]
            names = prefetch_display_names(members)
            for member in members:
                # Shown by the row's quick-view toggle.
                member.quick_rows = _diff_rows(member, names=names)
            sections.append({
                'key': key,
                'title': title,
                'counted': counted,
                'items': items,
                'total': total,
                'page': page,
                'pages': pages,
                'page_param': f'{key}_page',
                'prev_query': _with_param(request, f'{key}_page', page - 1) if page > 1 else None,
                'next_query': _with_param(request, f'{key}_page', page + 1) if page < pages else None,
            })
        from . import worksheet

        field_work = field_work_total = None
        if worksheet.enabled():
            open_tasks = worksheet.open_tasks()
            field_work_total = open_tasks.count()
            field_work = list(open_tasks.select_related('site', 'rack', 'change_request').order_by('site__name', 'rack__name', 'pk')[:50])
        return render(request, 'netbox_change_approval/inbox.html', {
            'sections': sections,
            'inbox_count': sum(s['total'] for s in sections if s['counted']),
            'field_work': field_work,
            'field_work_total': field_work_total,
        })

    def post(self, request):
        # "Clear" on the Rejected section.
        if request.user.is_authenticated and request.POST.get('clear') == 'rejected':
            inbox.mark_rejections_seen(request.user)
        return redirect(reverse('plugins:netbox_change_approval:changerequest_inbox'))


class _BulkDecisionView(ConditionalLoginRequiredMixin, View):
    """
    "Approve Selected" / "Reject Selected" under the Approvals list. The list
    posts the ticked pks (or `_all` for everything matching the filter); this
    shows a confirmation page, and its own post (with `_confirm`) decides.

    Only pending requests you may decide are touched. A ticked request in a
    bulk batch takes the rest of its batch along, as on its own page. Each
    request (or batch) is decided on its own: one that can't be applied
    (changed since submission, say) stays pending and the others go ahead.
    """
    decision = None  # 'approve' or 'reject'
    template_name = 'netbox_change_approval/changerequest_bulk_decision.html'

    def _selected(self, request):
        queue = permissions.approver_queue(
            request.user, ChangeRequest.objects.filter(status=StatusChoices.PENDING),
        )
        if request.POST.get('_all'):
            return queue.filter(
                pk__in=filtersets.ChangeRequestFilterSet(request.GET, queue, request=request).qs.values('pk'),
            )
        return queue.filter(pk__in=[pk for pk in request.POST.getlist('pk') if str(pk).isdigit()])

    @staticmethod
    def _groups(selected):
        """Selected requests grouped into units decided together (a batch, or
        a single request), in list order."""
        groups, seen = [], set()
        for change_request in selected.order_by('-submitted_at'):
            if change_request.pk in seen:
                continue
            members = _batch_targets(change_request)
            seen.update(cr.pk for cr in members)
            groups.append(members)
        return groups

    def _return_url(self, request):
        # A plain "starts with one slash" check lets "/\evil.example" through,
        # which browsers treat like "//evil.example".
        return _safe_return_url(
            request.POST.get('return_url'),
            reverse('plugins:netbox_change_approval:changerequest_approvals'),
        )

    def post(self, request):
        selected = self._selected(request)
        return_url = self._return_url(request)
        if not selected.exists():
            messages.warning(request, _('No requests selected that you can decide.'))
            return redirect(return_url)

        form = forms.ChangeRequestBulkRejectForm(request.POST) if self.decision == 'reject' else None
        if '_confirm' not in request.POST or (form is not None and not form.is_valid()):
            return render(request, self.template_name, {
                'decision': self.decision,
                'selected': list(selected.order_by('-submitted_at').select_related('submitted_by', 'content_type')),
                'pks': list(selected.values_list('pk', flat=True)),
                'form': form if form is not None and '_confirm' in request.POST else forms.ChangeRequestBulkRejectForm(),
                'return_url': return_url,
            })

        done, recorded, failed = 0, 0, []
        for group in self._groups(selected):
            if self.decision == 'reject':
                try:
                    with transaction.atomic():
                        for change_request in removal_order(group):
                            change_request.reject(request.user, form.cleaned_data['comment'])
                except (AlreadyDecided, CreatedObjectInUse) as e:
                    failed.append(f'#{group[0].pk}: {e}')
                    continue
                done += len(group)
                continue
            try:
                result = approvals.approve(group, request.user)
                if result.applied:
                    done += len(group)
                else:
                    recorded += len(group)
            except approvals.AlreadyDecided as e:
                failed.append(f'#{group[0].pk}: {e}')
            except ChangeConflict as e:
                failed.append(f'#{group[0].pk}: {e}')
            except (ValidationError, FieldDoesNotExist) as e:
                failed.append(f"#{group[0].pk}: {'; '.join(getattr(e, 'messages', [str(e)]))}")

        if self.decision == 'reject':
            messages.success(request, _('Rejected %(count)d change(s).') % {'count': done})
        elif done:
            messages.success(request, _('Approved and applied %(count)d change(s).') % {'count': done})
        if recorded:
            messages.info(
                request,
                _('Approval recorded on %(count)d change(s) that need more approvers before they are applied.')
                % {'count': recorded},
            )
        if failed:
            messages.error(
                request,
                _('Not changed: %(errors)s') % {'errors': ' '.join(failed)},
            )
        return redirect(return_url)


@register_model_view(ChangeRequest, 'bulk_approve', path='approve', detail=False)
class ChangeRequestBulkApproveView(_BulkDecisionView):
    decision = 'approve'


@register_model_view(ChangeRequest, 'bulk_reject', path='reject', detail=False)
class ChangeRequestBulkRejectView(_BulkDecisionView):
    decision = 'reject'


#
# Changelog and journal tabs of a change request
#

class _VisibleRequestOnly:
    """NetBox adds Changelog and Journal tabs to every model. For a change
    request they show what it asks for (the changelog keeps its diff), so
    they follow the same visibility rule as the request page itself
    (permissions.can_view_change_request), not only NetBox's view permission
    on change requests."""
    def get(self, request, model, **kwargs):
        change_request = get_object_or_404(ChangeRequest, **kwargs)
        if not permissions.can_view_change_request(request.user, change_request):
            raise Http404
        return super().get(request, model, **kwargs)


class ChangeRequestChangeLogView(_VisibleRequestOnly, generic.ObjectChangeLogView):
    pass


class ChangeRequestJournalView(_VisibleRequestOnly, generic.ObjectJournalView):
    pass


def _guard_feature_tabs():
    from netbox.registry import registry

    replacements = {'changelog': ChangeRequestChangeLogView, 'journal': ChangeRequestJournalView}
    for entry in registry['views'].get('netbox_change_approval', {}).get('changerequest', []):
        if entry['name'] in replacements:
            entry['view'] = replacements[entry['name']]


_guard_feature_tabs()


class AwayView(ConditionalLoginRequiredMixin, View):
    """Your "away" status and stand-in (absence.py), and whom you cover for."""
    template_name = 'netbox_change_approval/away.html'

    def _render(self, request, form, current):
        return render(request, self.template_name, {
            'form': form,
            'current': current,
            'covering': absence.covering_for(request.user),
        })

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        from django.utils import timezone

        current = absence.current_for(request.user)
        initial = {'starts': timezone.localdate()}
        if current is not None:
            initial = {'stand_in': current.stand_in, 'starts': current.starts, 'ends': current.ends, 'note': current.note}
        return self._render(request, forms.AwayForm(initial=initial, user=request.user), current)

    def post(self, request):
        from .models import ApproverAbsence

        if not request.user.is_authenticated:
            return redirect(reverse('login'))
        if request.POST.get('clear'):
            ApproverAbsence.objects.filter(user=request.user).delete()
            messages.success(request, _('You are no longer marked as away.'))
            return redirect(reverse('plugins:netbox_change_approval:away'))
        form = forms.AwayForm(request.POST, user=request.user)
        if not form.is_valid():
            return self._render(request, form, absence.current_for(request.user))
        ApproverAbsence.objects.update_or_create(user=request.user, defaults=form.cleaned_data)
        messages.success(request, _('Saved. %(who)s decides for you while you are away.') % {
            'who': form.cleaned_data['stand_in'],
        })
        return redirect(reverse('plugins:netbox_change_approval:away'))


class WorkSheetView(ConditionalLoginRequiredMixin, View):
    """Field work sheet (worksheet.py): physical jobs from applied requests,
    per site and rack, to tick off on site."""
    template_name = 'netbox_change_approval/worksheet.html'

    def get(self, request):
        from dcim.models import Rack, Site

        from .models import WorkTask
        from .worksheet import open_tasks

        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        tasks = open_tasks()
        site = Site.objects.filter(pk=request.GET.get('site') or None).first() if request.GET.get('site', '').isdigit() else None
        rack = Rack.objects.filter(pk=request.GET.get('rack')).first() if request.GET.get('rack', '').isdigit() else None
        done = WorkTask.objects.filter(change_request__status=StatusChoices.APPLIED, done_at__isnull=False)
        if site is not None:
            tasks, done = tasks.filter(site=site), done.filter(site=site)
        if rack is not None:
            tasks, done = tasks.filter(rack=rack), done.filter(rack=rack)
        related = ('site', 'rack', 'change_request', 'done_by')
        groups = {}
        for task in tasks.select_related(*related).order_by('site__name', 'rack__name', 'pk'):
            groups.setdefault((task.site, task.rack), []).append(task)
        return render(request, self.template_name, {
            'groups': [{'site': key[0], 'rack': key[1], 'tasks': value} for key, value in groups.items()],
            'done': done.select_related(*related).order_by('-done_at')[:20],
            'site': site,
            'rack': rack,
            'open_count': tasks.count(),
        })

    def post(self, request):
        from django.utils import timezone

        from .models import WorkTask

        if not request.user.is_authenticated:
            return redirect(reverse('login'))
        task = WorkTask.objects.filter(pk=request.POST.get('task') or 0).first()
        if task is not None:
            if request.POST.get('undo'):
                task.done_by, task.done_at = None, None
            else:
                task.done_by, task.done_at = request.user, timezone.now()
            task.save(update_fields=['done_by', 'done_at'])
        if request.POST.get('back') == 'inbox':
            return redirect(reverse('plugins:netbox_change_approval:changerequest_inbox') + '#nbca-section-field-work')
        url = reverse('plugins:netbox_change_approval:worksheet')
        query = request.POST.get('query', '')
        return redirect(f'{url}?{query}' if query else url)


class ChangeSetListView(ConditionalLoginRequiredMixin, View):
    """Your change sets, and starting a new one (changesets.py)."""
    def get(self, request):
        from .changesets import active_for
        from .models import ChangeSet

        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        return render(request, 'netbox_change_approval/changeset_list.html', {
            'form': forms.ChangeSetForm(),
            'change_sets': ChangeSet.objects.filter(created_by=request.user)[:50],
            'active': active_for(request),
        })

    def post(self, request):
        from .changesets import activate
        from .models import ChangeSet

        if not request.user.is_authenticated:
            return redirect(reverse('login'))
        form = forms.ChangeSetForm(request.POST)
        if not form.is_valid():
            messages.error(request, _('Give the change set a name.'))
            return redirect(reverse('plugins:netbox_change_approval:changeset_list'))
        change_set = ChangeSet.objects.create(created_by=request.user, **form.cleaned_data)
        activate(request, change_set)
        messages.success(request, _(
            'Change set "%(name)s" started. Changes you make now go into it; submit it when done.'
        ) % {'name': change_set.name})
        return redirect(request.POST.get('return_url') or change_set.get_absolute_url())


class ChangeSetView(ConditionalLoginRequiredMixin, View):
    """One change set: its changes, and Submit / Discard / Continue / Leave."""
    def _get(self, request, pk):
        from .models import ChangeSet

        return get_object_or_404(ChangeSet, pk=pk)

    def get(self, request, pk):
        from . import changesets

        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        change_set = self._get(request, pk)
        members = list(change_set.requests().select_related('content_type', 'submitted_by').order_by('pk'))
        visible = [cr for cr in members if permissions.can_view_change_request(request.user, cr)]
        if change_set.created_by_id != request.user.pk and not visible:
            messages.warning(request, _("You don't have access to this change set."))
            return redirect(reverse('plugins:netbox_change_approval:changerequest_inbox'))
        for cr in visible:
            cr.quick_rows = _diff_rows(cr)
        pending = [cr for cr in members if cr.status == StatusChoices.PENDING]
        return render(request, 'netbox_change_approval/changeset.html', {
            'object': change_set,
            'members': visible,
            'pending': pending,
            'is_owner': change_set.created_by_id == request.user.pk,
            'is_active': (changesets.active_for(request) or None) == change_set,
            'can_decide': bool(pending) and _can_decide(request.user, pending[0]),
        })

    def post(self, request, pk):
        from . import changesets

        change_set = self._get(request, pk)
        if change_set.created_by_id != request.user.pk:
            messages.error(request, _('Only its author can change a change set.'))
            return redirect(change_set.get_absolute_url())
        action = request.POST.get('action')
        if change_set.status != change_set.DRAFT and action != 'leave':
            messages.warning(request, _('This change set was already submitted.'))
            return redirect(change_set.get_absolute_url())
        if action == 'continue':
            changesets.activate(request, change_set)
            messages.success(request, _('Changes you make now go into "%(name)s".') % {'name': change_set})
        elif action == 'leave':
            changesets.leave(request)
            messages.info(request, _('Left "%(name)s". Your changes stay in it until you submit or discard it.') % {
                'name': change_set,
            })
        elif action == 'submit':
            if not change_set.requests().filter(status=StatusChoices.PENDING).exists():
                messages.warning(request, _('There is nothing in this change set to submit yet.'))
                return redirect(change_set.get_absolute_url())
            count = changesets.submit(change_set)
            changesets.leave(request)
            messages.success(request, _('Submitted "%(name)s" (%(count)d changes) for approval.') % {
                'name': change_set, 'count': count,
            })
        elif action == 'discard':
            count = changesets.discard(change_set, request.user)
            changesets.leave(request)
            messages.success(request, _('Discarded the change set and withdrew %(count)d change(s).') % {'count': count})
            return redirect(reverse('plugins:netbox_change_approval:changeset_list'))
        return redirect(change_set.get_absolute_url())
