"""
The one place that decides, for a given instance + action + request, whether a
write may proceed immediately or must be queued (or auto-approved-and-applied)
instead. Used by both validators.ApprovalGateValidator (create/update) and
signals.gate_delete (delete) so the two paths can't drift apart.
"""
from django.utils import timezone

from core.models import ObjectType

from . import permissions, serialization
from .context import applying_change, current_batch
from .exceptions import ObjectLocked
from .models import (
    ActionChoices, ApprovalRoutingRule, AutoApprovalRule, ChangeRequest, ExemptModel, StatusChoices,
)


def _resolve_approver_group(instance):
    from netbox.plugins.utils import get_plugin_config

    for rule in ApprovalRoutingRule.objects.filter(enabled=True).order_by('priority'):
        if rule.matches(instance):
            return rule.approver_group

    from users.models import Group

    default_name = get_plugin_config('netbox_change_approval', 'default_approver_group')
    try:
        return Group.objects.get(name=default_name)
    except Group.DoesNotExist:
        return None


def _matching_auto_approval_rule(instance, action):
    for rule in AutoApprovalRule.objects.filter(enabled=True).order_by('priority'):
        if rule.matches(instance, action=action):
            return rule
    return None


def _is_exempt(instance, content_type, action):
    """
    Live, DB-backed exemption check — see ExemptModel's docstring for how this
    differs from (and complements) the static PLUGINS_CONFIG['exempt_models']
    list discovery.py uses to decide validator/signal *registration* at process
    start. Checked on every gate() call specifically so toggling a model's
    exemption on or off takes effect immediately, no restart needed.
    """
    rows = ExemptModel.objects.filter(content_type=content_type, enabled=True)
    check_new_state = action == ActionChoices.UPDATE
    return any(row.matches(instance, check_new_state=check_new_state) for row in rows)


def gate(instance, action, request):
    """
    Returns None if the write should proceed exactly as given: bypass granted, a
    true no-op update, we're already inside ChangeRequest.apply(), an
    auto-approval rule matched (the request is logged as applied and the write
    already in flight is the approved one), or — for CREATE
    specifically, even when *not* auto-approved — see "CREATE is different"
    below.

    Otherwise returns a ChangeRequest the caller must abort the write for:
    - UPDATE: returned *saved* (safe — this runs before any
      transaction.atomic() the view opens; see DEVELOPER.md).
    - DELETE, not auto-approved: returned *unsaved*. This runs inside NetBox's
      delete-collector's own transaction.atomic() (netbox/models/deletion.py),
      which the caller is about to roll back by raising — anything saved here
      would be rolled back right along with it. The caller (signals.gate_delete)
      must persist it only after that exception has unwound past that
      transaction (see ChangeApprovalMiddleware.process_exception). See
      DEVELOPER.md "Delete gating and the transaction problem".

    Raises ObjectLocked if the object already has an unresolved pending request
    (that existing request is already saved from a prior call — nothing new to
    persist either way).

    CREATE is different: the point is for the new object to show up immediately
    as a real Rack/Device/IP address/etc., tagged "Pending Approval", so a
    second person doesn't submit a duplicate while the first is still waiting —
    see README "Pending creates are visible immediately". So for CREATE, "needs
    approval" is *also* a "proceed" case: gate() stashes the not-yet-saved
    ChangeRequest on `instance._nbca_pending_create` and returns None. A post_save
    receiver (signals.py) picks it up once the real pk exists, saves it with
    that object_id, and applies the tag. ChangeRequest.apply() for a CREATE just
    removes the tag (the data's already live); .reject() deletes the object.
    """
    if applying_change.get():
        return None

    model = type(instance)
    content_type = ObjectType.objects.get_for_model(model)

    if _is_exempt(instance, content_type, action):
        return None

    user = getattr(request, 'user', None)
    if user is not None and not user.is_authenticated:
        user = None

    if permissions.has_bypass(user, instance, check_new_state=action == ActionChoices.UPDATE):
        return None

    if ChangeRequest.is_object_locked(content_type, instance.pk):
        existing = ChangeRequest.objects.get(
            content_type=content_type, object_id=instance.pk, status='pending',
        )
        raise ObjectLocked(existing)

    if action == ActionChoices.UPDATE:
        old_instance = model.objects.get(pk=instance.pk)
        diff = serialization.snapshot_update(old_instance, instance)
        if not diff:
            return None  # nothing actually changed — let the no-op save through
    else:  # CREATE or DELETE — snapshot current/requested state for the record
        diff = serialization.snapshot_create(instance)

    change_request = ChangeRequest(
        content_type=content_type,
        object_id=instance.pk,
        object_repr=str(instance)[:300],
        action=action,
        diff=diff,
        submitted_by=user,
        batch_id=current_batch.get(),
        approver_group=_resolve_approver_group(instance),
    )

    auto_rule = _matching_auto_approval_rule(instance, action)
    if auto_rule is not None:
        change_request.auto_approved = True
        change_request.status = StatusChoices.APPLIED
        change_request.applied_at = timezone.now()

        if action == ActionChoices.DELETE:
            # The delete already in flight *is* the approved action. We can't
            # apply() it (that would mean deleting this exact row a second time,
            # re-entrantly, from within its own pre_delete signal — see
            # DEVELOPER.md) — just log it and let the original delete proceed.
            change_request.save()
            return None
        if action == ActionChoices.CREATE:
            # Let the original save proceed normally (this *is* the approved
            # write) and pick up the real pk in post_save — see docstring above.
            instance._nbca_pending_create = change_request
            return None

        # UPDATE: same as DELETE/CREATE above, the save already in flight *is*
        # the approved write. Log it and let it proceed, so the user gets
        # NetBox's normal "saved" flow instead of a form error. The validator
        # picks the marker up to show which rule approved it.
        change_request.save()
        instance._nbca_auto_applied = change_request
        return None

    if action == ActionChoices.CREATE:
        instance._nbca_pending_create = change_request  # unsaved — see docstring above
        return None

    if action == ActionChoices.DELETE:
        return change_request  # unsaved — see docstring above

    change_request.save()
    return change_request
