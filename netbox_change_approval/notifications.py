"""
NetBox's own in-app notifications (the bell icon in the top bar) for change
requests. No email or Slack: those stay with NetBox Event Rules (README).

Who hears about what:
- New or resubmitted request: every active member of its approver group
  except the submitter.
- Approved, rejected, sent back or expired: the submitter (not when they did
  it themselves, e.g. retracting to edit).
- New comment: the submitter, the approver group and earlier commenters,
  except whoever wrote it.

When a request stops being pending, approvers' unread "needs approval"
notifications for it are removed: there's nothing left for them to do.

A bulk batch is one notification, on its first request. NetBox keeps one
notification per object and user, so a newer one replaces the older one (and
shows as unread again).

Sent after the database transaction commits, so a change that is rolled back
(a failed bulk approval, say) never notifies anyone.
"""
from django.db import transaction
from django.db.models.signals import post_save
from django.utils.translation import gettext_lazy as _

from .models import ChangeRequest, StatusChoices

NEEDS_APPROVAL = 'netbox_change_approval.needs_approval'
APPROVED = 'netbox_change_approval.approved'
REJECTED = 'netbox_change_approval.rejected'
SENT_BACK = 'netbox_change_approval.sent_back'
EXPIRED = 'netbox_change_approval.expired'
COMMENTED = 'netbox_change_approval.commented'


def register_event_types():
    from netbox.events import (
        EVENT_TYPE_KIND_DANGER, EVENT_TYPE_KIND_INFO, EVENT_TYPE_KIND_SUCCESS, EVENT_TYPE_KIND_WARNING, EventType,
    )
    from netbox.registry import registry

    for name, text, kind in (
        (NEEDS_APPROVAL, _('Change request needs your approval'), EVENT_TYPE_KIND_WARNING),
        (APPROVED, _('Change request approved'), EVENT_TYPE_KIND_SUCCESS),
        (REJECTED, _('Change request rejected'), EVENT_TYPE_KIND_DANGER),
        (SENT_BACK, _('Change request sent back to you'), EVENT_TYPE_KIND_WARNING),
        (EXPIRED, _('Change request expired'), EVENT_TYPE_KIND_DANGER),
        (COMMENTED, _('New comment on change request'), EVENT_TYPE_KIND_INFO),
    ):
        if name not in registry['event_types']:
            EventType(name, text, kind=kind).register()


def _label(change_request):
    """What the notification shows: "#12 Update rack R1", plus how many more
    for a batch."""
    text = f'#{change_request.pk} {change_request.get_action_display()} {change_request.object_repr}'
    if change_request.batch_id:
        others = ChangeRequest.objects.filter(batch_id=change_request.batch_id).exclude(pk=change_request.pk).count()
        if others:
            text = f'{text} (+{others})'
    return text[:200]


def notify(users, change_request, event_type):
    """Give each user (once, skipping inactive ones) a notification about
    `change_request`, replacing any earlier one they have for it."""
    from django.contrib.contenttypes.models import ContentType
    from extras.models import Notification

    content_type = ContentType.objects.get_for_model(ChangeRequest)
    label = _label(change_request)
    seen = set()
    for user in users:
        if user is None or user.pk in seen or not user.is_active:
            continue
        seen.add(user.pk)
        Notification.objects.filter(object_type=content_type, object_id=change_request.pk, user=user).delete()
        notification = Notification.objects.create(
            user=user, object_type=content_type, object_id=change_request.pk, event_type=event_type,
        )
        # Notification.save() always stores str(object); ours reads better
        # without the status, which would soon be out of date.
        Notification.objects.filter(pk=notification.pk).update(object_repr=label)


def _approvers(change_request):
    if change_request.approver_group_id is None:
        from .gating import _default_approver_group
        group = _default_approver_group()
    else:
        group = change_request.approver_group
    if group is None:
        return []
    return list(group.users.filter(is_active=True))


def _is_batch_lead(change_request, status):
    """Only the lowest-numbered request of a batch (in `status`) notifies."""
    if not change_request.batch_id:
        return True
    lead = ChangeRequest.objects.filter(batch_id=change_request.batch_id, status=status).order_by('pk').first()
    return lead is None or lead.pk == change_request.pk


def _clear_approver_notifications(change_request):
    from django.contrib.contenttypes.models import ContentType
    from extras.models import Notification

    Notification.objects.filter(
        object_type=ContentType.objects.get_for_model(ChangeRequest),
        object_id=change_request.pk,
        event_type=NEEDS_APPROVAL,
        read__isnull=True,
    ).delete()


def _after_commit(pk, old_status):
    change_request = ChangeRequest.objects.filter(pk=pk).select_related(
        'submitted_by', 'decided_by', 'approver_group',
    ).first()
    if change_request is None:
        return
    status = change_request.status
    submitter = change_request.submitted_by

    if old_status == StatusChoices.PENDING and status != StatusChoices.PENDING:
        _clear_approver_notifications(change_request)

    if not _is_batch_lead(change_request, status):
        return

    if status == StatusChoices.PENDING:
        if change_request.auto_approved:
            return
        recipients = [u for u in _approvers(change_request) if u.pk != change_request.submitted_by_id]
        notify(recipients, change_request, NEEDS_APPROVAL)
        return

    if submitter is None or change_request.decided_by_id == submitter.pk:
        return  # nobody to tell, or they did it themselves
    if status == StatusChoices.APPLIED and not change_request.auto_approved:
        notify([submitter], change_request, APPROVED)
    elif status == StatusChoices.REJECTED:
        notify([submitter], change_request, REJECTED)
    elif status == StatusChoices.RETURNED:
        notify([submitter], change_request, SENT_BACK)
    elif status == StatusChoices.EXPIRED:
        notify([submitter], change_request, EXPIRED)


def change_request_saved(sender, instance, created, **kwargs):
    old_status = getattr(instance, '_nbca_loaded_status', None)
    new_status = instance.status
    instance._nbca_loaded_status = new_status
    if not created and old_status == new_status:
        return
    pk = instance.pk
    transaction.on_commit(lambda: _after_commit(pk, old_status))


def comment_added(comment):
    change_request = comment.change_request
    recipients = []
    if change_request.submitted_by_id:
        recipients.append(change_request.submitted_by)
    recipients.extend(_approvers(change_request))
    recipients.extend(c.author for c in change_request.comments.select_related('author') if c.author_id)
    recipients = [u for u in recipients if u is not None and u.pk != comment.author_id]
    pk = comment.pk

    def send():
        from .models import ChangeRequestComment
        if ChangeRequestComment.objects.filter(pk=pk).exists():
            notify(recipients, change_request, COMMENTED)
    transaction.on_commit(send)


def connect():
    register_event_types()
    post_save.connect(
        change_request_saved, sender=ChangeRequest,
        dispatch_uid='netbox_change_approval:notifications',
    )
