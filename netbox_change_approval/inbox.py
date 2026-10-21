"""
The Approval Inbox: one page (and a navbar button with a count) for what is
waiting on *you*.

- Needs your decision: pending requests routed to a group you're in.
- Waiting for your revision: your requests sent back by an approver, or
  retracted by you to edit.
- Rejected: your rejected requests you haven't opened yet.
- Waiting on others: your own pending requests. Shown for reference, not
  counted, since there's nothing for you to do.
"""
from .models import ChangeRequest, StatusChoices
from . import permissions


def _decide(user):
    return permissions.approver_queue(
        user, ChangeRequest.objects.filter(status=StatusChoices.PENDING),
    ).order_by('submitted_at')


def _sent_back(user):
    return ChangeRequest.objects.filter(
        submitted_by=user, status=StatusChoices.RETURNED,
    ).order_by('-decided_at')


def _rejected(user):
    return ChangeRequest.objects.filter(
        submitted_by=user, status=StatusChoices.REJECTED, seen_by_submitter=False,
    ).order_by('-decided_at')


def _waiting(user):
    return ChangeRequest.objects.filter(
        submitted_by=user, status=StatusChoices.PENDING,
    ).order_by('-submitted_at')


def inbox_count(user):
    if not user.is_authenticated:
        return 0
    return _decide(user).count() + _sent_back(user).count() + _rejected(user).count()


def inbox_sections(user):
    """[(key, title, queryset, counted)], in display order."""
    from django.utils.translation import gettext as _

    return [
        ('decide', _('Needs your decision'), _decide(user).select_related('submitted_by', 'content_type'), True),
        ('sent_back', _('Waiting for your revision'), _sent_back(user).select_related('decided_by', 'content_type'), True),
        ('rejected', _('Rejected'), _rejected(user).select_related('decided_by', 'content_type'), True),
        ('waiting', _('Waiting on others'), _waiting(user).select_related('approver_group', 'content_type'), False),
    ]


def mark_rejections_seen(user, queryset=None):
    queryset = queryset if queryset is not None else ChangeRequest.objects.all()
    return queryset.filter(
        submitted_by=user, status=StatusChoices.REJECTED, seen_by_submitter=False,
    ).update(seen_by_submitter=True)
