from django.contrib import messages
from django.db.models.signals import post_delete, post_save, pre_delete
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext as _

from netbox.context import current_request

from . import permissions
from .bulk import wrap_model_delete
from .discovery import iter_gated_models
from .exceptions import ChangeApprovalRequired, ObjectLocked
from .gating import gate
from .models import ActionChoices, ApprovalRoutingRule, StatusChoices
from .tags import get_pending_tag


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

    try:
        change_request = gate(instance, ActionChoices.DELETE, request)
    except ObjectLocked as exc:
        raise ChangeApprovalRequired(exc.change_request, locked=True)

    if change_request is None:
        return  # proceed: bypass granted, or an auto-approval rule already logged it

    raise ChangeApprovalRequired(change_request)


def finish_pending_create(sender, instance, created, **kwargs):
    """
    The second half of CREATE gating (see gating.gate()'s docstring for why
    CREATE works differently from UPDATE/DELETE: the object is allowed to save
    for real immediately, so it's visible right away and a second person can't
    submit a duplicate). gate() stashes an unsaved ChangeRequest on the instance
    when this is needed; this is where it gets a real object_id and gets saved,
    and — if it's still pending (not auto-approved) — the object gets tagged.
    """
    change_request = getattr(instance, '_nbca_pending_create', None)
    if change_request is None:
        return
    del instance._nbca_pending_create
    if not created:
        return  # shouldn't happen (the marker is only set for genuine creates)

    change_request.object_id = instance.pk
    change_request.save()

    request = current_request.get()

    if change_request.status == StatusChoices.APPLIED:
        if request is not None:
            messages.success(
                request,
                f'{instance} created — auto-approved by rule (request #{change_request.pk}).',
            )
            # See ChangeApprovalMiddleware._drop_duplicate_create_message: without
            # this, the view's own generic "Created <Model> <obj>" message (queued
            # a moment from now, in ObjectEditView.form_valid(), right after this
            # save() returns) would show up as a second, strictly-less-informative
            # flash alongside the one just added above.
            request._nbca_suppress_default_create_message = True
        return

    instance.tags.add(get_pending_tag())
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
        )
        request._nbca_suppress_default_create_message = True


def _resync_approver_permission(sender, **kwargs):
    permissions.sync_is_approver_permission()


def connect():
    for model in iter_gated_models():
        dispatch_uid = f'netbox_change_approval:{model._meta.label}'
        pre_delete.connect(gate_delete, sender=model, dispatch_uid=f'{dispatch_uid}:delete')
        post_save.connect(finish_pending_create, sender=model, dispatch_uid=f'{dispatch_uid}:create')
        # Lets bulk delete queue every selected object, not just the first one.
        wrap_model_delete(model)

    # Keeps the "Approvals" nav item's backing permission (permissions.
    # IS_APPROVER_ACTION) in sync with whichever groups are approvers right
    # now, every time an admin adds/edits/deletes an ApprovalRoutingRule — see
    # sync_is_approver_permission()'s docstring. The *initial* sync (covering
    # process start and default_approver_group config changes, which don't go
    # through this model at all) happens in __init__.py's ready() instead,
    # since that one needs a migrated database to query against and this
    # module can be imported before one exists (e.g. during the first
    # `migrate` itself).
    post_save.connect(
        _resync_approver_permission, sender=ApprovalRoutingRule,
        dispatch_uid='netbox_change_approval:resync_approver_permission:save',
    )
    post_delete.connect(
        _resync_approver_permission, sender=ApprovalRoutingRule,
        dispatch_uid='netbox_change_approval:resync_approver_permission:delete',
    )


connect()
