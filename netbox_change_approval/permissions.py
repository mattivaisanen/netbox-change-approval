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

#: Unlike BYPASS_ACTION, admins never grant this one by hand —
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


def _viewable_requests_q(user, queryset):
    """
    A filter for the requests in `queryset` whose object this user may view
    in NetBox, the same way can_view_change_request() checks one request:
    model-level "view" permission (or EXEMPT_VIEW_PERMISSIONS), narrowed by
    the permission's constraints while the object exists. Without the
    constraints, someone allowed to see only tenant A's devices saw every
    device request in the list.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Q

    ct_ids = queryset.order_by().values_list('content_type_id', flat=True).distinct()
    q = Q(pk__in=[])
    for ct in ContentType.objects.filter(pk__in=list(ct_ids)):
        if not user.has_perm(f'{ct.app_label}.view_{ct.model}'):
            continue
        model = ct.model_class()
        restrict = getattr(getattr(model, 'objects', None), 'restrict', None)
        if model is None or restrict is None:
            q |= Q(content_type_id=ct.pk)
            continue
        q |= Q(content_type_id=ct.pk) & (
            Q(object_id__isnull=True)
            | Q(object_id__in=restrict(user, 'view').values('pk'))
            # Object deleted since: model-level permission decides.
            | ~Q(object_id__in=model.objects.values('pk'))
        )
    return q


def visible_change_requests(user, queryset):
    """
    Change requests a user may read: those about objects they're allowed to
    view in NetBox, plus their own submissions and anything routed to their
    approver groups (superusers see everything). A request's diff shows the
    object's field values, so it follows the same view permission as the object.
    Approve/reject/retract are checked separately in the views.
    """
    from django.db.models import Q

    if not user.is_authenticated:
        return queryset.none()
    if user.is_superuser:
        return queryset
    return queryset.filter(
        _viewable_requests_q(user, queryset)
        | Q(submitted_by=user)
        | Q(approver_group__in=_decide_group_ids(user))
    ).distinct()


def can_view_change_request(user, change_request):
    """Single-request check, including object-level permission constraints
    (e.g. "view VPN tunnels of tenant X only") when the object still exists."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or change_request.submitted_by_id == user.pk:
        return True
    if change_request.approver_group_id and change_request.approver_group_id in _decide_group_ids(user):
        return True
    ct = change_request.content_type
    if not user.has_perm(f'{ct.app_label}.view_{ct.model}'):
        return False
    model = ct.model_class()
    if model is None or change_request.object_id is None:
        return True
    if not model.objects.filter(pk=change_request.object_id).exists():
        return True  # object deleted since; model-level permission decides
    restrict = getattr(model.objects, 'restrict', None)
    if restrict is None:
        return True
    return restrict(user, 'view').filter(pk=change_request.object_id).exists()


def can_decide(user, change_request):
    """
    May `user` approve, reject or send back `change_request`? Superusers and
    members of the request's approver group may, except on their own requests:
    nobody signs off their own change, superusers included (they withdraw it
    with Retract instead).
    """
    if user is None or not user.is_authenticated:
        return False
    if change_request.submitted_by_id is not None and change_request.submitted_by_id == user.pk:
        return False
    if user.is_superuser:
        return True
    if change_request.approver_group_id is None:
        return False
    # A member of the group, or standing in for an away member (absence.py).
    return change_request.approver_group_id in _decide_group_ids(user)


def approver_queue(user, queryset):
    """The "Approvals" tab: pending work routed to a group this user is in
    (everything, for a superuser), minus the user's own requests (see
    can_decide) and requests they already approved that need more approvers."""
    from django.db.models import Exists, OuterRef

    from .models import ChangeRequestApproval

    if not user.is_authenticated:
        return queryset.none()
    queryset = queryset.exclude(submitted_by=user)
    # Already approved by this user, waiting for another approver: nothing
    # left for them to do.
    queryset = queryset.exclude(Exists(ChangeRequestApproval.objects.filter(
        change_request=OuterRef('pk'), user=user, revision=OuterRef('revision'),
    )))
    if user.is_superuser:
        return queryset
    return queryset.filter(approver_group__in=_decide_group_ids(user)).distinct()


def sync_is_approver_permission():
    """
    Keeps one ObjectPermission's `groups` in sync with "every Group that is
    currently an approver" — i.e. referenced by an enabled "Needs approval" ApprovalRule,
    or the configured default_approver_group (which also decides who approves
    when no rule matches — see gating._default_approver_group). Called after
    `migrate`, on each process's first request, on every ApprovalRule
    save/delete and when the default approver group is created, renamed or
    deleted (signals.py), so "Approvals" nav visibility
    (navigation.py, permissions=[f'...{IS_APPROVER_ACTION}_changerequest']) stays
    correct without anyone needing to grant this by hand — unlike
    BYPASS_ACTION, this one is entirely our own bookkeeping, not
    an admin-facing permission.
    """
    from core.models import ObjectType
    from netbox.plugins.utils import get_plugin_config
    from users.models import Group, ObjectPermission

    from .models import ApprovalRule, ChangeRequest, RuleOutcomeChoices

    group_ids = set(
        ApprovalRule.objects.filter(
            enabled=True, outcome=RuleOutcomeChoices.REQUIRE, approver_group__isnull=False,
        ).values_list('approver_group_id', flat=True)
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


def _decide_group_ids(user):
    from .absence import decide_group_ids

    return decide_group_ids(user)
