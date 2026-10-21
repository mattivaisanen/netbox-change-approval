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
to bypass only for one tenant).
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


def has_bypass(user, instance):
    if user is None or not user.is_authenticated:
        return False

    model = type(instance)
    perm = _perm_name(model, BYPASS_ACTION)

    if instance.pk is None:
        # Constraint-based scoping can't be evaluated against an unsaved instance
        # (there's no database row to filter); fall back to the unscoped check,
        # which still respects any ObjectPermission that has no constraints set.
        return user.has_perm(perm)

    return user.has_perm(perm, obj=instance)


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
