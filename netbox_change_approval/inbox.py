"""
The Approval Inbox: one page (and a navbar button with a count) for your
change requests and what is waiting on *you*. Replaces the old My Requests
page.

Counted (something for you to do):
- Needs your decision: pending requests routed to a group you're in.
- Waiting for your revision: your requests sent back by an approver, or
  retracted by you to edit.
- Rejected: your rejected requests you haven't opened yet.

Listed for reference, not counted:
- Waiting on others: your own pending requests.
- Approved: your requests that were applied.

A bulk edit/import/delete is one item (its requests share a batch_id), the
same way approving or rejecting it decides the whole batch.
"""
from . import permissions
from .models import ChangeRequest, StatusChoices

#: Items shown per section; the header shows the full count.
SECTION_LIMIT = 50


def _decide(user):
    return permissions.approver_queue(
        user, ChangeRequest.objects.filter(status=StatusChoices.PENDING),
    ).order_by('-submitted_at')


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


def _approved(user):
    return ChangeRequest.objects.filter(
        submitted_by=user, status=StatusChoices.APPLIED,
    ).order_by('-applied_at')


def item_count(queryset):
    """Number of inbox items: a whole batch counts as one."""
    singles = queryset.filter(batch_id__isnull=True).count()
    batches = queryset.exclude(batch_id__isnull=True).values('batch_id').distinct().count()
    return singles + batches


def group_items(queryset, limit=SECTION_LIMIT):
    """
    [{'lead': cr, 'members': [cr, ...]}], at most `limit` items, in the
    queryset's order. Requests sharing a batch_id become one item, with every
    member of that batch that is in `queryset`.
    """
    items, by_batch = [], {}
    for change_request in queryset[: limit * 4]:
        if change_request.batch_id in by_batch:
            continue
        if len(items) >= limit:
            break
        item = {'lead': change_request, 'members': [change_request]}
        if change_request.batch_id is not None:
            item['members'] = []  # filled below, with the whole batch
            by_batch[change_request.batch_id] = item
        items.append(item)
    batch_ids = [item['lead'].batch_id for item in items if item['lead'].batch_id]
    if batch_ids:
        for member in queryset.filter(batch_id__in=batch_ids).order_by('pk'):
            item = by_batch.get(member.batch_id)
            if item is not None:
                item['members'].append(member)
    return items


def inbox_count(user):
    if not user.is_authenticated:
        return 0
    return item_count(_decide(user)) + item_count(_sent_back(user)) + item_count(_rejected(user))


def inbox_sections(user):
    """[(key, title, queryset, counted)], in display order."""
    from django.utils.translation import gettext as _

    related = ('submitted_by', 'decided_by', 'approver_group', 'content_type')
    return [
        ('decide', _('Needs your decision'), _decide(user).select_related(*related), True),
        ('sent_back', _('Waiting for your revision'), _sent_back(user).select_related(*related), True),
        ('rejected', _('Rejected'), _rejected(user).select_related(*related), True),
        ('waiting', _('Waiting on others'), _waiting(user).select_related(*related), False),
        ('approved', _('Approved'), _approved(user).select_related(*related), False),
    ]


def mark_rejections_seen(user, queryset=None):
    queryset = queryset if queryset is not None else ChangeRequest.objects.all()
    return queryset.filter(
        submitted_by=user, status=StatusChoices.REJECTED, seen_by_submitter=False,
    ).update(seen_by_submitter=True)

