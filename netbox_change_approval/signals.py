from django.contrib import messages
from django.db import models
from django.db.models.signals import post_delete, post_save, pre_delete
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext as _

from netbox.context import current_request

from . import permissions
from .bulk import wrap_model_delete
from .discovery import is_gated_model, iter_gated_models
from .exceptions import ChangeApprovalRequired, ObjectLocked
from .gating import gate, take_pending_create
from .models import ActionChoices, ApprovalRule, StatusChoices


def gate_delete(sender, instance, **kwargs):
    """
    pre_delete fires for every delete regardless of call site (single-object
    ObjectDeleteView, BulkDeleteView, cascade deletes, management commands, ...).
    request is recovered from NetBox's own current_request ContextVar rather than a
    signal kwarg, since Django's signal doesn't carry one.

    Deliberately does *not* save the ChangeRequest gate() returns here — this runs
    inside NetBox's delete-collector transaction, which is exactly what raising
    ChangeApprovalRequired is about to roll back. See gating.gate()'s docstring and
    DEVELOPER.md "Delete gating and the transaction problem". Saving happens in
    ChangeApprovalMiddleware.process_exception(), after that rollback has already
    happened.
    """
    request = current_request.get()

    # Deleting one object deletes its dependents too (a device's interfaces, a
    # site's locations, ...), and Django sends pre_delete for those dependents
    # *before* the object itself. Gating each one on its own would queue
    # "delete interface eth0" instead of "delete device sw1". So when the delete
    # started from another object (`origin`), gate that object, once: the
    # dependents simply follow its outcome.
    origin = kwargs.get('origin')
    target = instance
    if isinstance(origin, models.Model) and origin is not instance:
        if not is_gated_model(type(origin)):
            return  # an ungated object's delete; its dependents go with it
        target = origin

    decided = getattr(target, _DELETE_DECIDED_ATTR, False)
    if decided:
        return  # already let through for this delete

    try:
        change_request = gate(target, ActionChoices.DELETE, request)
    except ObjectLocked as exc:
        _refuse_if_api_bulk(ChangeApprovalRequired(exc.change_request, locked=True))
        raise ChangeApprovalRequired(exc.change_request, locked=True)

    if change_request is None:
        # Proceed: bypass granted, or an auto-approval rule already logged it.
        # Remember it, so the object's other dependents (and the object itself)
        # don't run the check again or log a second auto-approved request.
        setattr(target, _DELETE_DECIDED_ATTR, True)
        return

    _refuse_if_api_bulk(ChangeApprovalRequired(change_request))
    raise ChangeApprovalRequired(change_request)


def _refuse_if_api_bulk(exc):
    """REST API bulk delete: fail this object with NetBox's own per-object
    error instead of queuing (see bulk.BatchCollector.REFUSE)."""
    from utilities.exceptions import AbortRequest

    from .bulk import BatchCollector
    from .context import batch_collector
    from .validators import refusal_message

    collector = batch_collector.get()
    if collector is not None and collector.mode == BatchCollector.REFUSE:
        raise AbortRequest(refusal_message(exc))


#: Set on an object once its delete has been let through (see gate_delete).
_DELETE_DECIDED_ATTR = '_nbca_delete_allowed'


def forget_delete_decision(sender, instance, **kwargs):
    """Once the object is gone, drop gate_delete's marker, so the same Python
    object can never carry an old decision into a later delete."""
    if getattr(instance, _DELETE_DECIDED_ATTR, False):
        delattr(instance, _DELETE_DECIDED_ATTR)


def finish_pending_create(sender, instance, created, **kwargs):
    """
    The second half of CREATE gating (see gating.gate()'s docstring for why
    CREATE works differently from UPDATE/DELETE: the object is allowed to save
    for real immediately, so it's visible right away and a second person can't
    submit a duplicate). gate() stashes an unsaved ChangeRequest on the instance
    when this is needed; this is where it gets a real object_id and gets saved,
    and — if it's still pending (not auto-approved) — the object gets tagged.
    """
    if not created:
        return  # the marker is only set for genuine creates
    request = current_request.get()
    change_request = take_pending_create(instance, request)
    if change_request is None:
        return

    change_request.object_id = instance.pk
    change_request.object_repr = str(instance)[:300]
    change_request.save()

    if change_request.status == StatusChoices.APPLIED:
        if request is not None:
            messages.success(
                request,
                f'{instance} created — auto-approved by rule {change_request.approving_rule} '
                f'(request #{change_request.pk}).',
                fail_silently=True,
            )
            # See ChangeApprovalMiddleware._drop_duplicate_create_message: without
            # this, the view's own generic "Created <Model> <obj>" message (queued
            # a moment from now, in ObjectEditView.form_valid(), right after this
            # save() returns) would show up as a second, strictly-less-informative
            # flash alongside the one just added above.
            request._nbca_suppress_default_create_message = True
        return

    if request is not None:
        usernames = change_request.approver_usernames
        if usernames:
            approver_lines = format_html_join('', '<br>{}', ((name,) for name in usernames))
        else:
            approver_lines = format_html('<br>{}', _('(none configured)'))
        messages.success(
            request,
            format_html(
                '<a href="{}">{}</a> awaiting approval<br>Approvers:{}',
                instance.get_absolute_url(), instance, approver_lines,
            ),
            fail_silently=True,
        )
        request._nbca_suppress_default_create_message = True


def label_activity(sender, instance, **kwargs):
    """After an auto-approved edit is saved, label the object's own activity
    entry with the change request (see gating._gate, AUTO + UPDATE). Runs after
    NetBox's own post_save receiver, which wrote that entry."""
    change_request = getattr(instance, '_nbca_activity_label', None)
    if change_request is None:
        return
    del instance._nbca_activity_label
    change_request.label_target_activity()


def _resync_approver_permission(sender, **kwargs):
    permissions.sync_is_approver_permission()


def _resync_on_group_change(sender, instance, raw=False, **kwargs):
    # Groups change rarely and the sync is a few queries, so any group change
    # (created, renamed to or away from the default name, deleted) resyncs.
    if not raw:
        permissions.sync_is_approver_permission()


def protect_open_requests_group(sender, instance, **kwargs):
    """A group can't be deleted while requests still wait on it: they would
    lose their approvers. Decided requests keep only its name."""
    from .models import ChangeRequest

    waiting = list(ChangeRequest.objects.filter(
        approver_group=instance, status__in=(StatusChoices.PENDING, StatusChoices.RETURNED),
    )[:50])
    if waiting:
        raise models.ProtectedError(
            _('Group {group} approves open change requests. Decide them first.').format(group=instance),
            waiting,
        )


def connect():
    for model in iter_gated_models():
        dispatch_uid = f'netbox_change_approval:{model._meta.label}'
        pre_delete.connect(gate_delete, sender=model, dispatch_uid=f'{dispatch_uid}:delete')
        post_delete.connect(forget_delete_decision, sender=model, dispatch_uid=f'{dispatch_uid}:delete_done')
        post_save.connect(finish_pending_create, sender=model, dispatch_uid=f'{dispatch_uid}:create')
        post_save.connect(label_activity, sender=model, dispatch_uid=f'{dispatch_uid}:activity')
        # Lets bulk delete queue every selected object, not just the first one.
        wrap_model_delete(model)

    # Keeps the "Approvals" nav item's backing permission (permissions.
    # IS_APPROVER_ACTION) in sync with whichever groups are approvers right
    # now, every time an admin adds/edits/deletes an ApprovalRule — see
    # sync_is_approver_permission()'s docstring. The initial sync runs after
    # `migrate` and on each process's first request (__init__.py, middleware).
    post_save.connect(
        _resync_approver_permission, sender=ApprovalRule,
        dispatch_uid='netbox_change_approval:resync_approver_permission:save',
    )
    post_delete.connect(
        _resync_approver_permission, sender=ApprovalRule,
        dispatch_uid='netbox_change_approval:resync_approver_permission:delete',
    )
    # The default approver group is named in the config, so it can be
    # created (or renamed to that name) after NetBox has started. Resync on
    # group changes too, or its members only see "Approvals" after a restart.
    from users.models import Group
    post_save.connect(
        _resync_on_group_change, sender=Group,
        dispatch_uid='netbox_change_approval:resync_approver_permission:group_save',
    )
    pre_delete.connect(
        protect_open_requests_group, sender=Group,
        dispatch_uid='netbox_change_approval:protect_open_requests_group',
    )
    post_delete.connect(
        _resync_on_group_change, sender=Group,
        dispatch_uid='netbox_change_approval:resync_approver_permission:group_delete',
    )

connect()
