"""
The Approval Inbox: one page (and a navbar button with a count) for your
change requests and what is waiting on *you*. Replaces the old My Requests
page.

Counted (something for you to do):
- Needs your decision: pending requests routed to a group you're in.
- Waiting for your revision: your requests sent back by an approver, or
  retracted by you to edit.
- Rejected: your rejected (or expired) requests you haven't opened yet.

Listed for reference, not counted:
- Waiting on others: your own pending requests.
- Approved: your requests that were applied.

A bulk edit/import/delete is one item (its requests share a batch_id), the
same way approving or rejecting it decides the whole batch.
"""
from . import permissions
from .models import ChangeRequest, StatusChoices

#: Items per page in each section; the header shows the full count.
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
        submitted_by=user, status__in=(StatusChoices.REJECTED, StatusChoices.EXPIRED), seen_by_submitter=False,
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


def group_items(queryset, limit=None, page=1):
    """
    [{'lead': cr, 'members': [cr, ...]}] for one page of `limit` items, in
    the queryset's order. Requests sharing a batch_id become one item (placed
    where its first request falls), with every member of that batch that is
    in `queryset`.
    """
    limit = limit or SECTION_LIMIT
    units = []  # (batch_id or None, lead pk), in order
    seen_batches = set()
    for pk, batch_id in queryset.values_list('pk', 'batch_id'):
        if batch_id is not None:
            if batch_id in seen_batches:
                continue
            seen_batches.add(batch_id)
        units.append((batch_id, pk))
    page = max(1, page)
    window = units[(page - 1) * limit: page * limit]
    if not window:
        return []

    leads = queryset.in_bulk([pk for _batch, pk in window])
    items, by_batch = [], {}
    for batch_id, pk in window:
        lead = leads.get(pk)
        if lead is None:
            continue
        item = {'lead': lead, 'members': [lead]}
        if batch_id is not None:
            item['members'] = []  # filled below, with the whole batch
            by_batch[batch_id] = item
        items.append(item)
    if by_batch:
        for member in queryset.filter(batch_id__in=list(by_batch)).order_by('pk'):
            by_batch[member.batch_id]['members'].append(member)
    return items


def page_count(total, limit=None):
    limit = limit or SECTION_LIMIT
    return max(1, -(-total // limit))


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
        ('rejected', _('Rejected or expired'), _rejected(user).select_related(*related), True),
        ('waiting', _('Waiting on others'), _waiting(user).select_related(*related), False),
        ('approved', _('Approved'), _approved(user).select_related(*related), False),
    ]


def mark_rejections_seen(user, queryset=None):
    queryset = queryset if queryset is not None else ChangeRequest.objects.all()
    return queryset.filter(
        submitted_by=user, status__in=(StatusChoices.REJECTED, StatusChoices.EXPIRED), seen_by_submitter=False,
    ).update(seen_by_submitter=True)

