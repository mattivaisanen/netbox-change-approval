from django.contrib import messages
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from netbox.object_actions import BulkExport
from netbox.views import generic
from utilities.forms.forms import ConfirmationForm
from utilities.views import ConditionalLoginRequiredMixin, register_model_view

from . import filtersets, forms, permissions, placement, serialization, tables
from .exceptions import ChangeConflict
from .models import ActionChoices, ApprovalRoutingRule, AutoApprovalRule, ChangeRequest, ExemptModel, StatusChoices

#
# Exempt models
#


@register_model_view(ExemptModel, 'list', path='', detail=False)
class ExemptModelListView(generic.ObjectListView):
    queryset = ExemptModel.objects.all()
    filterset = filtersets.ExemptModelFilterSet
    filterset_form = forms.ExemptModelFilterForm
    table = tables.ExemptModelTable


@register_model_view(ExemptModel)
class ExemptModelView(generic.ObjectView):
    queryset = ExemptModel.objects.all()
    template_name = 'netbox_change_approval/exemptmodel.html'


@register_model_view(ExemptModel, 'add', detail=False)
@register_model_view(ExemptModel, 'edit')
class ExemptModelEditView(generic.ObjectEditView):
    queryset = ExemptModel.objects.all()
    form = forms.ExemptModelForm


@register_model_view(ExemptModel, 'delete')
class ExemptModelDeleteView(generic.ObjectDeleteView):
    queryset = ExemptModel.objects.all()


#
# Approval routing rules
#


@register_model_view(ApprovalRoutingRule, 'list', path='', detail=False)
class ApprovalRoutingRuleListView(generic.ObjectListView):
    queryset = ApprovalRoutingRule.objects.all()
    filterset = filtersets.ApprovalRoutingRuleFilterSet
    filterset_form = forms.ApprovalRoutingRuleFilterForm
    table = tables.ApprovalRoutingRuleTable


@register_model_view(ApprovalRoutingRule)
class ApprovalRoutingRuleView(generic.ObjectView):
    queryset = ApprovalRoutingRule.objects.all()
    template_name = 'netbox_change_approval/approvalroutingrule.html'


@register_model_view(ApprovalRoutingRule, 'add', detail=False)
@register_model_view(ApprovalRoutingRule, 'edit')
class ApprovalRoutingRuleEditView(generic.ObjectEditView):
    queryset = ApprovalRoutingRule.objects.all()
    form = forms.ApprovalRoutingRuleForm


@register_model_view(ApprovalRoutingRule, 'delete')
class ApprovalRoutingRuleDeleteView(generic.ObjectDeleteView):
    queryset = ApprovalRoutingRule.objects.all()


#
# Auto-approval rules
#


@register_model_view(AutoApprovalRule, 'list', path='', detail=False)
class AutoApprovalRuleListView(generic.ObjectListView):
    queryset = AutoApprovalRule.objects.all()
    filterset = filtersets.AutoApprovalRuleFilterSet
    filterset_form = forms.AutoApprovalRuleFilterForm
    table = tables.AutoApprovalRuleTable


@register_model_view(AutoApprovalRule)
class AutoApprovalRuleView(generic.ObjectView):
    queryset = AutoApprovalRule.objects.all()
    template_name = 'netbox_change_approval/autoapprovalrule.html'


@register_model_view(AutoApprovalRule, 'add', detail=False)
@register_model_view(AutoApprovalRule, 'edit')
class AutoApprovalRuleEditView(generic.ObjectEditView):
    queryset = AutoApprovalRule.objects.all()
    form = forms.AutoApprovalRuleForm


@register_model_view(AutoApprovalRule, 'delete')
class AutoApprovalRuleDeleteView(generic.ObjectDeleteView):
    queryset = AutoApprovalRule.objects.all()


#
# Change requests
#


def _can_decide(user, change_request):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if change_request.approver_group_id is None:
        return False
    # Forward relation (user.groups), not approver_group.user_set: NetBox's own
    # Group model doesn't use Django's stock related_name (it's `users`, not
    # `user_set` — see users/models/users.py). Going through the user avoids
    # caring what it's called.
    return user.groups.filter(pk=change_request.approver_group_id).exists()


def _resolve_display(model, key, value):
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


def _diff_rows(change_request, conflict_fields=()):
    diff = change_request.diff
    if not isinstance(diff, dict):
        return []
    model = change_request.content_type.model_class()

    if change_request.action == ActionChoices.UPDATE:
        return [
            {
                'field': field,
                'label': _field_label(model, field),
                'old': _resolve_display(model, field, change.get('old')),
                'new': _resolve_display(model, field, change.get('new')),
                'conflict': field in conflict_fields,
            }
            for field, change in sorted(diff.items())
        ]
    if change_request.action == ActionChoices.CREATE:
        return [
            {'field': field, 'label': _field_label(model, field), 'old': None, 'new': _resolve_display(model, field, value)}
            for field, value in sorted(diff.items())
        ]
    # delete: diff is a full snapshot of what's being removed
    return [
        {'field': field, 'label': _field_label(model, field), 'old': _resolve_display(model, field, value), 'new': _('(deleted)')}
        for field, value in sorted(diff.items())
    ]


@register_model_view(ChangeRequest, 'list', path='', detail=False)
class ChangeRequestListView(generic.ObjectListView):
    queryset = ChangeRequest.objects.all()
    filterset = filtersets.ChangeRequestFilterSet
    filterset_form = forms.ChangeRequestFilterForm
    table = tables.ChangeRequestTable

    def get_queryset(self, request):
        # Everyone sees everything unless scoped: own submissions, or requests
        # routed to a group they're in. See permissions.visible_change_requests.
        return permissions.visible_change_requests(request.user, super().get_queryset(request))


@register_model_view(ChangeRequest, 'mine', path='mine', detail=False)
class MyChangeRequestsView(generic.ObjectListView):
    """"My Requests" nav tab: always exactly what this user submitted, regardless
    of view_all — unlike the main list, this one never shows anyone else's."""
    queryset = ChangeRequest.objects.all()
    filterset = filtersets.ChangeRequestFilterSet
    filterset_form = forms.ChangeRequestFilterForm
    table = tables.ChangeRequestTable
    template_name = 'netbox_change_approval/changerequest_mine.html'
    # Export only: no Add/Import (requests come from edits elsewhere) and no
    # bulk edit/delete, which also drops the selection checkboxes so both
    # tables have the same columns.
    actions = (BulkExport,)

    # Two tables on one page: "Waiting for decision" (this user's pending
    # requests, small, unpaginated) on top, and the normal NetBox list below for
    # everything already decided — that one keeps search, filters, paging and
    # export, which only work for one table per page.

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .filter(submitted_by=request.user)
            .exclude(status=StatusChoices.PENDING)
        )

    def get_extra_context(self, request):
        pending = ChangeRequest.objects.filter(
            submitted_by=request.user, status=StatusChoices.PENDING,
        ).order_by('-submitted_at')
        # Own prefix so its paging/sort parameters don't clash with the main table's.
        pending_table = tables.ChangeRequestTable(pending, prefix='pending_', orderable=False)
        pending_table.configure(request)  # applies the user's column choices
        if 'pk' in pending_table.columns.names():
            pending_table.columns.hide('pk')
        return {'pending_table': pending_table, 'pending_total': pending.count()}


@register_model_view(ChangeRequest, 'approvals', path='approvals', detail=False)
class ApprovalsQueueView(generic.ObjectListView):
    """"Approvals" nav tab: the actionable queue — pending requests routed to a
    group this user is in (or every pending request, with view_all)."""
    queryset = ChangeRequest.objects.filter(status=StatusChoices.PENDING)
    filterset = filtersets.ChangeRequestFilterSet
    filterset_form = forms.ChangeRequestFilterForm
    table = tables.ChangeRequestTable
    template_name = 'netbox_change_approval/changerequest_approvals.html'

    def get_queryset(self, request):
        return permissions.approver_queue(request.user, super().get_queryset(request))

    def get_extra_context(self, request):
        # Replaces the old top-right navbar ticker (see template_content.py) —
        # self.queryset is already this user's scoped, still-pending set at this
        # point (BaseMultiObjectView.dispatch() sets it before extra context is
        # built), so counting it is exactly what the old ticker counted.
        return {'pending_count': self.queryset.count()}


@register_model_view(ChangeRequest)
class ChangeRequestView(generic.ObjectView):
    queryset = ChangeRequest.objects.all()

    # No per-user scoping here: any logged-in user can open a single request
    # (e.g. from the banner on the object) and read its diff. Lists stay scoped:
    # My Requests is only your own, Approvals only what's routed to you, and
    # All Change Requests needs view_all. Approve/reject/retract keep their own
    # checks.

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
        return {
            'diff_rows': _diff_rows(instance, conflict_fields),
            'conflict_labels': [
                _field_label(instance.content_type.model_class(), f) for f in conflict_fields
            ],
            'object_missing': object_missing,
            'placement_rows': placement.placement_rows(instance),
            'batch_siblings': batch_siblings,
            'can_decide': _can_decide(request.user, instance),
            'can_retract': (
                instance.status == StatusChoices.PENDING and instance.submitted_by_id == request.user.pk
            ),
        }


def _batch_targets(change_request):
    if not change_request.batch_id:
        return [change_request]
    return list(ChangeRequest.objects.filter(batch_id=change_request.batch_id, status=StatusChoices.PENDING))


def _default_return_url():
    # Where a decision lands when nothing more specific was requested (e.g.
    # decided straight from the detail page) — the queue, not the just-decided
    # request's own detail page, so approvers keep moving through the list
    # instead of bouncing back to a dead end each time.
    return reverse('plugins:netbox_change_approval:changerequest_list')


class _ChangeRequestDecisionView(ConditionalLoginRequiredMixin, View):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.change_request = get_object_or_404(
            ChangeRequest.objects.filter(status=StatusChoices.PENDING), pk=kwargs['pk'],
        )

    def check_permission(self, request):
        if not _can_decide(request.user, self.change_request):
            messages.error(request, _("You are not a member of this request's approver group."))
            return redirect(self.change_request.get_absolute_url())
        return None


@register_model_view(ChangeRequest, 'approve')
class ChangeRequestApproveView(_ChangeRequestDecisionView):
    def get(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        targets = _batch_targets(self.change_request)
        return render(request, 'netbox_change_approval/changerequest_approve.html', {
            'object': self.change_request,
            'form': ConfirmationForm(initial=request.GET),
            'return_url': request.GET.get('return_url') or _default_return_url(),
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
            # rolls back every change already applied in this batch, and every
            # request stays pending.
            with transaction.atomic():
                for change_request in targets:
                    change_request.apply(user=request.user)
        except ChangeConflict as e:
            messages.error(request, _('Not applied: %(error)s Reject this request and resubmit it.') % {'error': e})
            return redirect(self.change_request.get_absolute_url())
        except (ValidationError, FieldDoesNotExist) as e:
            messages.error(request, _('Not applied: %(error)s') % {'error': '; '.join(getattr(e, 'messages', [str(e)]))})
            return redirect(self.change_request.get_absolute_url())
        messages.success(request, _('Approved and applied %(count)d change(s).') % {'count': len(targets)})
        return redirect(form.cleaned_data.get('return_url') or _default_return_url())


@register_model_view(ChangeRequest, 'reject')
class ChangeRequestRejectView(_ChangeRequestDecisionView):
    def get(self, request, pk):
        if (denied := self.check_permission(request)) is not None:
            return denied
        targets = _batch_targets(self.change_request)
        return render(request, 'netbox_change_approval/changerequest_reject.html', {
            'object': self.change_request,
            'form': forms.ChangeRequestRejectForm(initial=request.GET),
            'return_url': request.GET.get('return_url') or _default_return_url(),
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
                'return_url': request.POST.get('return_url') or _default_return_url(),
                'batch_count': len(_batch_targets(self.change_request)),
            })

        targets = _batch_targets(self.change_request)
        for change_request in targets:
            change_request.reject(request.user, form.cleaned_data['comment'])
        messages.success(request, _('Rejected %(count)d change(s).') % {'count': len(targets)})
        return redirect(form.cleaned_data.get('return_url') or _default_return_url())


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
            'return_url': request.GET.get('return_url') or _default_return_url(),
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
        for change_request in targets:
            change_request.retract(request.user)
        messages.success(request, _('Retracted %(count)d change(s).') % {'count': len(targets)})
        return redirect(form.cleaned_data.get('return_url') or _default_return_url())
