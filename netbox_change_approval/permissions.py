"""
Bypass permission check, built entirely on NetBox's own permission system — no new
permission model, no new admin screen. Confirmed against NetBox 4.2 source
(netbox/netbox/authentication/__init__.py, ObjectPermissionMixin.get_object_permissions):
a granted ObjectPermission with a custom action string "foo" on object type
<app_label>.<model> becomes the Django permission "<app_label>.foo_<model_name>",
checked the normal way via user.has_perm(perm, obj=instance).

To grant bypass: NetBox UI -> Admin -> Permissions -> Add, pick the object type(s),
enter "bypass_approval" under Additional actions, assign to a user/group, and
optionally scope it with the existing Constraints field (e.g. {"tenant__slug": "acme"}
to bypass only for one tenant). Constraints apply to creates and to the new values of
an update too — see has_bypass().
"""
BYPASS_ACTION = 'bypass_approval'

#: Same mechanism as bypass_approval, applied to ChangeRequest itself: grant via
#: Admin -> Permissions -> object type "Change Approval | Change Request" ->
#: additional action "view_all" -> assign to whichever users/groups should see
#: every request (e.g. a change-management admin team). Without it, a user only
#: sees requests they submitted themselves or that are routed to a group they're
#: in — see visible_change_requests().
VIEW_ALL_ACTION = 'view_all'

#: Unlike BYPASS_ACTION/VIEW_ALL_ACTION, admins never grant this one by hand —
#: see sync_is_approver_permission() below. It only exists so the "Approvals"
#: nav item (navigation.py) can be hidden from non-approvers via the normal
#: `permissions=[...]` check, which NetBox's nav only evaluates against real,
#: static Django permissions (see utilities/templatetags/navigation.py:nav()) —
#: there's no hook for "is this user in some currently-configured approver
#: group" otherwise.
IS_APPROVER_ACTION = 'is_approver'
_IS_APPROVER_SYNC_PERM_NAME = 'netbox_change_approval_is_approver_sync'


def _perm_name(model, action):
    return f'{model._meta.app_label}.{action}_{model._meta.model_name}'


def _bypass_constraints(user, perm):
    """
    The constraint sets of every bypass grant `user` holds for `perm`, read from
    NetBox's own permission backend (the same lists user.has_perm() filters on).
    Returns None when no configured backend exposes them.
    """
    from django.contrib.auth import get_backends

    from netbox.authentication import ObjectPermissionMixin

    found = False
    constraints = []
    for backend in get_backends():
        if isinstance(backend, ObjectPermissionMixin):
            found = True
            constraints.extend(backend.get_all_permissions(user).get(perm, []))
    return constraints if found else None


def _matches_bypass_in_memory(user, instance, constraints):
    """
    True if the in-memory (about to be saved) state of `instance` matches at
    least one grant's constraints. A grant with no constraints matches anything.
    The "$user" token is replaced the same way NetBox's backend replaces it.
    """
    from users.constants import CONSTRAINT_TOKEN_USER

    from .matching import _matches_new_state

    if any(not c for c in constraints):
        return True

    def _replace(value):
        if isinstance(value, list):
            return [user.pk if v == CONSTRAINT_TOKEN_USER else v for v in value]
        return user.pk if value == CONSTRAINT_TOKEN_USER else value

    resolved = [{k: _replace(v) for k, v in c.items()} for c in constraints]
    return _matches_new_state(instance, resolved)


def has_bypass(user, instance, check_new_state=False):
    """
    True if `user` may write `instance` without approval.

    NetBox's user.has_perm(perm) with no object returns True for *any* grant,
    constrained or not, and has_perm(perm, obj=...) checks the row as stored in
    the database. Neither is enough on its own:

    - Create (no pk yet): the grant's constraints are checked against the
      in-memory object. A bypass scoped to tenant "acme" only covers new
      objects for tenant "acme".
    - Update (`check_new_state=True`): the stored row *and* the new values must
      both match, so an "acme only" bypass can't move an object to another tenant.
    - Delete: the stored row must match.

    A constraint that can't be evaluated in memory counts as "no bypass", so the
    change goes to an approver instead.
    """
    if user is None or not user.is_authenticated:
        return False

    model = type(instance)
    perm = _perm_name(model, BYPASS_ACTION)

    if not user.has_perm(perm):
        return False
    if user.is_active and user.is_superuser:
        return True

    if instance.pk is not None and not user.has_perm(perm, obj=instance):
        return False
    if instance.pk is not None and not check_new_state:
        return True

    constraints = _bypass_constraints(user, perm)
    if constraints is None:
        # No NetBox permission backend to read constraints from; nothing more
        # we can check. has_perm() above already passed.
        return True
    return _matches_bypass_in_memory(user, instance, constraints)


def visible_change_requests(user, queryset):
    """
    Scope a ChangeRequest queryset to what `user` is allowed to see: everything,
    for a superuser or anyone granted the view_all custom action (see
    VIEW_ALL_ACTION above); otherwise only requests they submitted themselves, or
    that are routed to an approver group they belong to.
    """
    from django.db.models import Q

    from .models import ChangeRequest

    if not user.is_authenticated:
        return queryset.none()

    if user.has_perm(_perm_name(ChangeRequest, VIEW_ALL_ACTION)):
        return queryset

    return queryset.filter(
        Q(submitted_by=user) | Q(approver_group__in=user.groups.all())
    ).distinct()


def approver_queue(user, queryset):
    """
    Like visible_change_requests, but for the "Approvals" tab specifically:
    things routed to a group this user is in — not also their own submissions,
    which belong on the separate "My Requests" tab instead.
    """
    from .models import ChangeRequest

    if not user.is_authenticated:
        return queryset.none()

    if user.has_perm(_perm_name(ChangeRequest, VIEW_ALL_ACTION)):
        return queryset

    return queryset.filter(approver_group__in=user.groups.all()).distinct()


def sync_is_approver_permission():
    """
    Keeps one ObjectPermission's `groups` in sync with "every Group that is
    currently an approver" — i.e. referenced by an *enabled* ApprovalRoutingRule,
    or the configured default_approver_group (which also decides who approves
    when no rule matches — see gating._resolve_approver_group). Called from
    __init__.py's ready() at process start, and again on every
    ApprovalRoutingRule save/delete, so "Approvals" nav visibility
    (navigation.py, permissions=[f'...{IS_APPROVER_ACTION}_changerequest']) stays
    correct without anyone needing to grant this by hand — unlike
    BYPASS_ACTION/VIEW_ALL_ACTION, this one is entirely our own bookkeeping, not
    an admin-facing permission.
    """
    from core.models import ObjectType
    from netbox.plugins.utils import get_plugin_config
    from users.models import Group, ObjectPermission

    from .models import ApprovalRoutingRule, ChangeRequest

    group_ids = set(
        ApprovalRoutingRule.objects.filter(enabled=True).values_list('approver_group_id', flat=True)
    )
    default_name = get_plugin_config('netbox_change_approval', 'default_approver_group')
    if default_name:
        group_ids.update(Group.objects.filter(name=default_name).values_list('pk', flat=True))

    perm, _created = ObjectPermission.objects.get_or_create(
        name=_IS_APPROVER_SYNC_PERM_NAME,
        defaults={'enabled': True, 'actions': [IS_APPROVER_ACTION]},
    )
    if not _created and (not perm.enabled or perm.actions != [IS_APPROVER_ACTION]):
        perm.enabled = True
        perm.actions = [IS_APPROVER_ACTION]
        perm.save()
    perm.object_types.set([ObjectType.objects.get_for_model(ChangeRequest)])
    perm.groups.set(group_ids)
