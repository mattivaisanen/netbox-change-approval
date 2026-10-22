"""
The one place that decides, for a given instance + action + request, whether a
write may proceed immediately or must be queued (or auto-approved-and-applied)
instead. Used by both validators.ApprovalGateValidator (create/update) and
signals.gate_delete (delete) so the two paths can't drift apart.
"""
from django.utils import timezone

from core.models import ObjectType

from . import permissions, serialization
from .context import applying_change, batch_collector, current_batch
from .exceptions import ObjectLocked
from .models import ActionChoices, ApprovalRule, ChangeRequest, RuleOutcomeChoices, StatusChoices


def _default_approver_group():
    from users.models import Group

    from .plugin_settings import get_setting

    default_name = get_setting('default_approver_group')
    try:
        return Group.objects.get(name=default_name)
    except Group.DoesNotExist:
        return None


def _first_matching_rule(instance, action):
    """The first enabled ApprovalRule (by priority) that matches, or None."""
    rules = ApprovalRule.objects.filter(enabled=True).select_related(
        'content_type', 'approver_group',
    ).order_by('priority', 'pk')
    for rule in rules:
        if rule.matches(instance, action=action):
            return rule
    return None


#: Request attribute: {model label: [unsaved ChangeRequest, ...]} for REST API creates.
PENDING_CREATES_ATTR = '_nbca_pending_creates'


def stash_pending_create(instance, change_request, request):
    """
    Hand a create's request to signals.finish_pending_create, which saves it
    once the object has a pk. The web UI saves the instance it validated, so
    the instance carries it. The REST API validates a throwaway copy and saves
    a new instance built from the same data, so for API requests it's also
    kept on the request, in order, per model.
    """
    instance._nbca_pending_create = change_request
    if request is None:
        return
    from utilities.api import is_api_request

    try:
        if not is_api_request(request):
            return
    except Exception:
        return
    pending = getattr(request, PENDING_CREATES_ATTR, None)
    if pending is None:
        pending = {}
        try:
            setattr(request, PENDING_CREATES_ATTR, pending)
        except AttributeError:
            return
    pending.setdefault(instance._meta.label_lower, []).append(change_request)


def take_pending_create(instance, request):
    """The request stash_pending_create() kept for this newly saved object, or None."""
    change_request = getattr(instance, '_nbca_pending_create', None)
    queue = (getattr(request, PENDING_CREATES_ATTR, None) or {}).get(instance._meta.label_lower) \
        if request is not None else None
    if change_request is not None:
        del instance._nbca_pending_create
        if queue and change_request in queue:
            queue.remove(change_request)
        return change_request
    if queue:
        return queue.pop(0)
    return None


def gate(instance, action, request):
    """See _gate() below. An UPDATE that may proceed is marked, so the save()
    that follows isn't gated a second time by backstop.py."""
    result = _gate(instance, action, request)
    if action == ActionChoices.CREATE:
        instance._nbca_create_checked = True  # see backstop.gate_plain_create
    if result is None and action == ActionChoices.UPDATE:
        from .backstop import mark_validated
        mark_validated(instance, request)
    return result


def _gate(instance, action, request):
    """
    Returns None if the write should proceed exactly as given: bypass granted, a
    true no-op update, we're already inside ChangeRequest.apply(), an
    auto-approval rule matched (the request is logged as applied and the write
    already in flight is the approved one), or — for CREATE
    specifically, even when *not* auto-approved — see "CREATE is different"
    below.

    Otherwise returns a ChangeRequest the caller must abort the write for:
    - UPDATE: returned *saved* (safe — this runs before any
      transaction.atomic() the view opens; see DEVELOPER.md). Inside a bulk
      action (context.batch_collector set) it's returned *unsaved* instead —
      see bulk.py.
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
    as a real Rack/Device/IP address/etc., marked pending, so a
    second person doesn't submit a duplicate while the first is still waiting —
    see README "Pending creates are visible immediately". So for CREATE, "needs
    approval" is *also* a "proceed" case: gate() stashes the not-yet-saved
    ChangeRequest on `instance._nbca_pending_create` and returns None. A post_save
    receiver (signals.py) picks it up once the real pk exists, saves it with
    that object_id. ChangeRequest.apply() for a CREATE just closes the request
    (the data's already live); .reject() deletes the object.
    """
    if applying_change.get():
        return None
    from .branching import outside_main
    if outside_main():
        return None  # netbox-branching handles it; see branching.py

    model = type(instance)
    content_type = ObjectType.objects.get_for_model(model)

    # One rule list, first match wins (see models.ApprovalRule). "Skip
    # approval" is checked before anything else, as exemptions always were.
    rule = _first_matching_rule(instance, action)
    if rule is not None and rule.outcome == RuleOutcomeChoices.SKIP:
        return None

    user = getattr(request, 'user', None)
    if user is not None and not user.is_authenticated:
        user = None

    if permissions.has_bypass(user, instance, check_new_state=action == ActionChoices.UPDATE):
        return None

    # A component (interface, port, IP on its interface, ...) of a new
    # device/VM that is still pending belongs to that request (components.py).
    from . import components
    handled, result = components.gate_component(instance, action, user, request)
    if handled:
        return result

    if ChangeRequest.is_object_locked(content_type, instance.pk):
        existing = ChangeRequest.objects.get(
            content_type=content_type, object_id=instance.pk, status='pending',
        )
        # The submitter editing their own pending request updates it instead
        # (see amend.py). Everyone else is still blocked.
        from . import amend
        if action == ActionChoices.UPDATE and amend.amendable(existing, user, request) \
                and amend.amending_here(existing, request):
            return amend.amend(existing, instance, request)
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
        approver_group=(
            rule.approver_group
            if rule is not None and rule.outcome == RuleOutcomeChoices.REQUIRE and rule.approver_group_id
            else _default_approver_group()
        ),
    )
    from .approvals import apply_rule_settings
    apply_rule_settings(change_request, rule)

    if rule is not None and rule.outcome == RuleOutcomeChoices.AUTO:
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
            stash_pending_create(instance, change_request, request)
            return None

        # UPDATE: same as DELETE/CREATE above, the save already in flight *is*
        # the approved write. Log it and let it proceed, so the user gets
        # NetBox's normal "saved" flow instead of a form error. The validator
        # picks the marker up to show which rule approved it.
        # Saved before the real write, so NetBox has no activity entry for the
        # object yet. Log quietly; signals.label_activity puts the label on the
        # object's own entry once the save has written it.
        change_request._nbca_log_quietly = True
        change_request.save()
        instance._nbca_activity_label = change_request
        instance._nbca_auto_applied = change_request
        return None

    if action == ActionChoices.CREATE:
        stash_pending_create(instance, change_request, request)  # unsaved — see docstring above
        return None

    if action == ActionChoices.DELETE:
        return change_request  # unsaved — see docstring above

    if batch_collector.get() is not None:
        # Bulk edit/import: the object's savepoint is about to be rolled back;
        # bulk.BatchCollector saves the request after that. See bulk.py.
        return change_request

    change_request.save()
    return change_request
