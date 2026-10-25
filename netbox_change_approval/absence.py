"""
Backup approvers: an approver who is away names a stand-in.

While the absence is active (from its start date through its last day), the
stand-in can decide requests routed to the away approver's groups, as if they
were a member: they appear in the stand-in's Approvals list, inbox and
dashboard widget, and the stand-in gets the "needs approval" notifications
instead of the away approver. Nobody can decide their own request, stand-in
or not. Set on the "Away" page (Change Approval menu).
"""
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone


def _active(today=None):
    from .models import ApproverAbsence

    today = today or timezone.localdate()
    return ApproverAbsence.objects.filter(starts__lte=today).filter(Q(ends__isnull=True) | Q(ends__gte=today))


def covered_group_ids(user):
    """Groups `user` decides for as a stand-in right now."""
    from users.models import Group

    if user is None or not user.is_authenticated:
        return set()
    away_ids = _active().filter(stand_in=user).values_list('user_id', flat=True)
    # Through the user's own `groups` field: NetBox's Group model names the
    # reverse lookup `user` in queries but `users` as an attribute, so
    # `users__in` fails with a FieldError.
    return set(Group.objects.filter(
        pk__in=get_user_model().objects.filter(pk__in=away_ids).values('groups'),
    ).values_list('pk', flat=True))


def decide_group_ids(user):
    """Groups whose requests `user` may decide: their own, plus those of
    approvers they stand in for."""
    if user is None or not user.is_authenticated:
        return set()
    return set(user.groups.values_list('pk', flat=True)) | covered_group_ids(user)


def notify_users(group):
    """Who should hear about a request for `group`: its active members who
    aren't away, plus the stand-ins of those who are."""
    members = list(group.users.filter(is_active=True))
    absences = {a.user_id: a for a in _active().filter(user__in=members).select_related('stand_in')}
    result = []
    for member in members:
        absence = absences.get(member.pk)
        if absence is None:
            result.append(member)
        elif absence.stand_in.is_active:
            result.append(absence.stand_in)
    return result


def current_for(user):
    """The user's own absence (active or planned), or None."""
    from .models import ApproverAbsence

    if user is None or not user.is_authenticated:
        return None
    return ApproverAbsence.objects.filter(user=user).select_related('stand_in').first()


def covering_for(user):
    """Active absences where `user` is the stand-in."""
    if user is None or not user.is_authenticated:
        return []
    return list(_active().filter(stand_in=user).select_related('user'))
