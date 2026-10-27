"""
Notifications for change requests: NetBox's own in-app notifications (the
bell icon in the top bar), and NetBox events that Event Rules can turn into
emails, chat messages or other webhooks.

Only the people a request concerns hear about it: its submitter and its
approver group (stand-ins in place of away members, see absence.py).

- New or resubmitted request: the approver group, except the submitter.
- Partly approved (a rule needs more approvers): the submitter.
- Approved, rejected, sent back or expired: the submitter (not when they did
  it themselves, e.g. retracting to edit).
- New comment: the submitter and the approver group, except whoever wrote it.
- Retracted while waiting (also "retract and edit", which sends the request
  back to its own submitter): the approver group, except the submitter (a
  NetBox event only, no bell notification: their "needs approval" one just
  goes).

Comments also fire NetBox's own "Object updated" event on the request, so an
Event Rule on "Object updated" hears about them too (with `last_comment`).

Each notification names the request, the object and who did what ("#12
Update device sw1 · rejected by bob: wrong rack"). Each also fires a NetBox
event of the same type on the change request, with everything a message
needs in its data (see event_data()), including who should receive it, so
an Event Rule with a webhook can e-mail or post it. See docs/usage.md "Getting
notified".

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
PARTLY_APPROVED = 'netbox_change_approval.partly_approved'
SCHEDULED = 'netbox_change_approval.scheduled'
SCHEDULE_FAILED = 'netbox_change_approval.schedule_failed'
RETRACTED = 'netbox_change_approval.retracted'


def register_event_types():
    from netbox.events import (
        EVENT_TYPE_KIND_DANGER, EVENT_TYPE_KIND_INFO, EVENT_TYPE_KIND_SUCCESS, EVENT_TYPE_KIND_WARNING, EventType,
    )
    from netbox.registry import registry

    class TranslatedEventType(EventType):
        # NetBox prints an event with str(event), and its __str__ returns the
        # text as is. Ours is a lazy translation (so each user sees their own
        # language), which Python rejects as a __str__ result: the notification
        # bell's drop-down failed to load. Turn it into plain text here.
        def __str__(self):
            return str(self.text)

    for name, text, kind in (
        (NEEDS_APPROVAL, _('Change request needs your approval'), EVENT_TYPE_KIND_WARNING),
        (APPROVED, _('Change request approved'), EVENT_TYPE_KIND_SUCCESS),
        (REJECTED, _('Change request rejected'), EVENT_TYPE_KIND_DANGER),
        (SENT_BACK, _('Change request sent back to you'), EVENT_TYPE_KIND_WARNING),
        (EXPIRED, _('Change request expired'), EVENT_TYPE_KIND_DANGER),
        (COMMENTED, _('New comment on change request'), EVENT_TYPE_KIND_INFO),
        (PARTLY_APPROVED, _('Change request approved, needs more approvers'), EVENT_TYPE_KIND_INFO),
        (SCHEDULED, _('Change request approved, scheduled'), EVENT_TYPE_KIND_SUCCESS),
        (SCHEDULE_FAILED, _('Scheduled change could not be applied'), EVENT_TYPE_KIND_DANGER),
        (RETRACTED, _('Change request retracted'), EVENT_TYPE_KIND_INFO),
    ):
        if name not in registry['event_types']:
            TranslatedEventType(name, text, kind=kind).register()


def _label(change_request):
    """What the notification shows: "#12 Update rack R1", plus how many more
    for a batch."""
    text = f'#{change_request.pk} {change_request.get_action_display()} {change_request.object_repr}'
    if change_request.batch_id:
        others = ChangeRequest.objects.filter(batch_id=change_request.batch_id).exclude(pk=change_request.pk).count()
        if others:
            text = f'{text} (+{others})'
    return text[:200]


def _person(user):
    if user is None:
        return None
    if not hasattr(user, 'pk'):  # DeletedUser: only the kept name
        return {'id': None, 'username': user.get_username(), 'name': user.get_username(), 'email': ''}
    return {
        'id': user.pk,
        'username': user.username,
        'name': user.get_full_name() or user.username,
        'email': user.email or '',
    }


def absolute_url(path):
    """`path` with the NetBox address in front, when the netbox_url setting
    (configuration.py) gives one: webhooks run outside a web request, so the
    plugin can't know the address users reach NetBox at."""
    from .plugin_settings import get_setting

    base = (get_setting('netbox_url') or '').rstrip('/')
    return f'{base}{path}' if base else path


def who_to_notify(change_request):
    """
    The people a change request concerns right now, for webhooks on NetBox's
    own "object created/updated" events: its approver group while it waits
    or once its submitter withdraws it, its submitter once it is decided. []
    when nobody needs to hear (an auto-approved request, a draft change set,
    a decision the submitter made themselves).
    """
    if change_request.auto_approved:
        return []
    if _withdrawn(change_request):
        return _approvers_except_submitter(change_request)
    if change_request.status == StatusChoices.PENDING:
        from .changesets import is_draft

        if is_draft(change_request):
            return []
        return _approvers_except_submitter(change_request)
    submitter = change_request.submitted_by
    if submitter is None or change_request.decided_by_id == submitter.pk:
        return []
    if change_request.status in (
        StatusChoices.APPLIED, StatusChoices.REJECTED, StatusChoices.RETURNED, StatusChoices.EXPIRED,
    ):
        return [submitter] if submitter.is_active else []
    return []


def event_data(change_request, event_type, recipients, detail='', actor=None, comment=''):
    """
    The data of the NetBox event fired with a notification: everything a
    message about it needs, for Event Rules (conditions) and webhooks (body
    templates as {{ data.<key> }}).
    """
    from netbox.registry import registry

    event = registry['event_types'].get(event_type)
    changes = changes_of(change_request)
    title = str(event) if event else event_type
    return {
        'message': message_text(change_request, title, actor, comment=comment),
        'revision_changes': revision_changes(change_request),
        'id': change_request.pk,
        'display': _label(change_request),
        'url': change_request.get_absolute_url(),
        'link': absolute_url(change_request.get_absolute_url()),
        'event': str(event) if event else event_type,
        'detail': detail,
        'action': change_request.action,
        'status': change_request.status,
        'revision': change_request.revision,
        'object_type': f'{change_request.content_type.app_label}.{change_request.content_type.model}',
        'object_id': change_request.object_id,
        'object': change_request.object_repr,
        'changes': changes,
        'submitted_by': _person(change_request.submitted_by_label),
        'submitted_at': change_request.submitted_at.isoformat() if change_request.submitted_at else None,
        'approver_group': change_request.approver_group_label or None,
        'approvers': [_person(u) for u in _approvers(change_request)],
        'approvals_needed': change_request.required_approvals,
        'by': _person(actor),
        'comment': comment,
        'reason': change_request.reason,
        'recipients': [_person(u) for u in recipients],
        'recipient_emails': ';'.join(u.email for u in recipients if u.email),
    }


def changes_of(change_request):
    """The request's changes as [{field, old, new}], all text."""
    from .views import _diff_rows

    try:
        return [
            {'field': str(row['label']), 'old': None if row['old'] is None else str(row['old']),
             'new': None if row['new'] is None else str(row['new'])}
            for row in _diff_rows(change_request)
        ]
    except Exception:
        return []


def revision_changes(change_request, names=None):
    """What a resubmitted request changes compared to the revision before
    it was sent back, as [{field, old, new}]: `old` is what the previous
    revision proposed, `new` what this one proposes. [] for a first
    revision. `names`: see views.prefetch_display_names."""
    from .models import ActionChoices
    from .views import _diff_rows, mark_revision_changes

    if change_request.revision <= 1:
        return []
    try:
        rows = mark_revision_changes(change_request, _diff_rows(change_request, names=names), names)
    except Exception:
        return []
    side = 'old' if change_request.action == ActionChoices.DELETE else 'new'

    def text(value):
        return None if value is None else str(value)

    return [
        {'field': str(row['label']), 'old': text(row['was']), 'new': text(row[side])}
        for row in rows if row.get('revised')
    ]


def _object_type_name(change_request):
    model = change_request.content_type.model_class() if change_request.content_type_id else None
    return str(model._meta.verbose_name) if model is not None else change_request.content_type.model


def message_text(change_request, title, actor, comment=''):
    """
    A short ready-to-send notice for webhooks ({{ data.message }}), so people
    know to look in NetBox, from the "Webhook message" setting (see
    message_template.py). The changes themselves are in `changes`.
    """
    from . import message_template
    from .plugin_settings import get_setting

    by = _person(actor) if actor is not None else None
    submitter = _person(change_request.submitted_by_label)
    context = {
        'by': by['name'] if by else '',
        'by_email': by['email'] if by else '',
        'title': str(title),
        'action': str(change_request.get_action_display()),
        'object_type': _object_type_name(change_request),
        'object': change_request.object_repr,
        'comment': comment or '',
        'link': absolute_url(change_request.get_absolute_url()),
        'id': change_request.pk,
        'status': change_request.status,
        'reason': change_request.reason or '',
        'submitted_by': submitter['name'] if submitter else '',
        'submitted_by_email': submitter['email'] if submitter else '',
        'approver_group': change_request.approver_group_label or '',
    }
    return message_template.render(get_setting('webhook_message'), context)


def fire_event(change_request, event_type, recipients, detail='', actor=None, comment=''):
    """Run NetBox Event Rules for this notification (webhooks, scripts, ...)."""
    import logging

    try:
        from core.models import ObjectType
        from extras.events import EventContext, flush_events

        context = EventContext(
            object_type=ObjectType.objects.get_for_model(ChangeRequest),
            object_id=change_request.pk,
            object=change_request,
            event_type=event_type,
            snapshots=None,
            user=actor or change_request.submitted_by,
            data=event_data(change_request, event_type, recipients, detail, actor, comment),
        )
        flush_events([context])
    except Exception:
        logging.getLogger('netbox_change_approval.notifications').exception(
            'Event rules for change request #%s failed', change_request.pk,
        )


def notify(users, change_request, event_type, detail='', actor=None, comment=''):
    """Give each user (once, skipping inactive ones) a notification about
    `change_request`, replacing any earlier one they have for it, and fire
    the matching NetBox event for Event Rules."""
    from django.contrib.contenttypes.models import ContentType
    from extras.models import Notification

    content_type = ContentType.objects.get_for_model(ChangeRequest)
    label = _label(change_request)
    if detail:
        label = f'{label} \u00b7 {detail}'[:200]
    recipients, seen = [], set()
    for user in users:
        if user is None or user.pk in seen or not user.is_active:
            continue
        seen.add(user.pk)
        recipients.append(user)
        Notification.objects.filter(object_type=content_type, object_id=change_request.pk, user=user).delete()
        notification = Notification.objects.create(
            user=user, object_type=content_type, object_id=change_request.pk, event_type=event_type,
        )
        # Notification.save() always stores str(object); ours reads better
        # without the status, which would soon be out of date.
        Notification.objects.filter(pk=notification.pk).update(object_repr=label)
    if recipients:
        fire_event(change_request, event_type, recipients, detail, actor, comment)


def _said(who, text, limit=120):
    text = (text or '').strip().replace('\n', ' ')
    if len(text) > limit:
        text = text[:limit - 1] + '\u2026'
    return f'{who}: {text}' if text else str(who)


def _withdrawn(change_request):
    """Its submitter took it back: retracted, or retracted to edit (sent
    back to themselves, see views.ChangeRequestRetractView)."""
    if change_request.status == StatusChoices.RETRACTED:
        return True
    return (change_request.status == StatusChoices.RETURNED and change_request.submitted_by_id is not None
            and change_request.decided_by_id == change_request.submitted_by_id)


def _approvers_except_submitter(change_request):
    return [u for u in _approvers(change_request) if u.pk != change_request.submitted_by_id]


def _approvers(change_request):
    if change_request.approver_group_id is None:
        from .gating import _default_approver_group
        group = _default_approver_group()
    else:
        group = change_request.approver_group
    if group is None:
        return []
    from .absence import notify_users
    return notify_users(group)


def _is_batch_lead(change_request, status):
    """Only the lowest-numbered request of a batch (in `status`) notifies.
    Per approver group: one bulk edit can need approval from several groups
    (different rules per tenant, say), and each group must hear about its
    part."""
    if not change_request.batch_id:
        return True
    lead = ChangeRequest.objects.filter(
        batch_id=change_request.batch_id, status=status, approver_group_id=change_request.approver_group_id,
    ).order_by('pk').first()
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

    if _withdrawn(change_request):
        # Approvers had it waiting: tell their webhooks it is gone. A request
        # sent back first was no longer waiting on them.
        from .changesets import is_draft
        if old_status == StatusChoices.PENDING and not change_request.auto_approved and not is_draft(change_request):
            recipients = [u for u in _approvers_except_submitter(change_request) if u.is_active]
            if recipients:
                who = change_request.decided_by or _('the submitter')
                fire_event(change_request, RETRACTED, recipients,
                           detail=str(_('retracted by {who}').format(who=who)), actor=change_request.decided_by)
        return

    if status == StatusChoices.PENDING:
        if change_request.auto_approved:
            return
        from .changesets import is_draft
        if is_draft(change_request):
            return  # a draft change set notifies when it is submitted
        recipients = [u for u in _approvers(change_request) if u.pk != change_request.submitted_by_id]
        who = change_request.submitted_by or _('unknown user')
        detail = (_('resubmitted by {who}') if change_request.revision > 1 else _('from {who}')).format(who=who)
        detail = _said(detail, change_request.reason) if change_request.reason else str(detail)
        notify(recipients, change_request, NEEDS_APPROVAL, detail=detail, actor=change_request.submitted_by)
        return

    if submitter is None or change_request.decided_by_id == submitter.pk:
        return  # nobody to tell, or they did it themselves
    decider = change_request.decided_by
    comment = change_request.decision_comment
    if status == StatusChoices.APPLIED and not change_request.auto_approved:
        notify([submitter], change_request, APPROVED,
               detail=str(_('approved by {who}').format(who=decider or _('an approver'))), actor=decider)
    elif status == StatusChoices.REJECTED:
        notify([submitter], change_request, REJECTED,
               detail=_said(_('rejected by {who}').format(who=decider or _('an approver')), comment),
               actor=decider, comment=comment)
    elif status == StatusChoices.RETURNED:
        notify([submitter], change_request, SENT_BACK,
               detail=_said(_('sent back by {who}').format(who=decider or _('an approver')), comment),
               actor=decider, comment=comment)
    elif status == StatusChoices.EXPIRED:
        notify([submitter], change_request, EXPIRED, detail=str(_('nobody decided it in time')))


def change_request_saved(sender, instance, created, **kwargs):
    old_status = getattr(instance, '_nbca_loaded_status', None)
    new_status = instance.status
    instance._nbca_loaded_status = new_status
    if not created and old_status == new_status:
        return
    pk = instance.pk
    transaction.on_commit(lambda: _after_commit(pk, old_status))


def request_updated(change_request):
    """The submitter updated their pending request (amend.py): tell the
    approvers again, the same way as a new request."""
    pk = change_request.pk
    transaction.on_commit(lambda: _after_commit(pk, None))


def comment_added(comment):
    """Tell the submitter and the approver group (not other commenters, and
    not the author)."""
    change_request = comment.change_request
    recipients = []
    if change_request.submitted_by_id:
        recipients.append(change_request.submitted_by)
    recipients.extend(_approvers(change_request))
    recipients = [u for u in recipients if u is not None and u.pk != comment.author_id]
    pk = comment.pk
    detail = _said(comment.author or _('deleted user'), comment.text)

    def send():
        from .models import ChangeRequestComment
        if ChangeRequestComment.objects.filter(pk=pk).exists():
            notify(recipients, change_request, COMMENTED, detail=detail, actor=comment.author, comment=comment.text)
            fire_object_updated(change_request, recipients, actor=comment.author, comment=comment.text)
    transaction.on_commit(send)


def fire_object_updated(change_request, recipients, actor=None, comment=''):
    """Run Event Rules on NetBox's own "Object updated" for `change_request`
    with `recipients` in its data, for a change NetBox doesn't see as an
    edit of the request itself (a new comment)."""
    import logging

    try:
        from core.events import OBJECT_UPDATED
        from core.models import ObjectType
        from extras.events import EventContext, flush_events, serialize_for_event

        current = ChangeRequest.objects.select_related('submitted_by', 'decided_by', 'approver_group').get(
            pk=change_request.pk,
        )
        current._nbca_recipients = [u for u in recipients if u is not None and u.is_active]
        current._nbca_message = message_text(
            current, str(_('New comment on a change request')), actor, comment=comment,
        )
        context = EventContext(
            object_type=ObjectType.objects.get_for_model(ChangeRequest),
            object_id=current.pk,
            object=current,
            event_type=OBJECT_UPDATED,
            snapshots=None,
            user=actor or current.submitted_by,
            data=serialize_for_event(current),
        )
        flush_events([context])
    except Exception:
        logging.getLogger('netbox_change_approval.notifications').exception(
            'Event rules for change request #%s failed', change_request.pk,
        )


def scheduled(change_request, user, when):
    """Approved and scheduled (scheduling.py): tell the submitter when it
    will be applied."""
    from django.utils import timezone as tz
    from django.utils.formats import date_format

    if change_request.submitted_by_id is None or change_request.submitted_by_id == getattr(user, 'pk', None):
        return
    pk = change_request.pk
    detail = str(_('approved by {who}, applies {when}').format(
        who=user, when=date_format(tz.localtime(when), 'SHORT_DATETIME_FORMAT'),
    ))

    def send():
        current = ChangeRequest.objects.filter(pk=pk).select_related('submitted_by').first()
        if current is not None:
            notify([current.submitted_by], current, SCHEDULED, detail=detail, actor=user)
    transaction.on_commit(send)


def schedule_failed(change_request, scheduler, error):
    """A scheduled request couldn't be applied when due: tell the submitter
    and whoever scheduled it."""
    current = ChangeRequest.objects.filter(pk=change_request.pk).select_related('submitted_by').first()
    if current is None:
        return
    notify([current.submitted_by, scheduler], current, SCHEDULE_FAILED,
           detail=_said(_('not applied'), error), actor=scheduler, comment=error)


def partly_approved(change_request, user, have, need):
    """An approval that doesn't yet apply the request (it needs more
    approvers): tell the submitter."""
    if change_request.submitted_by_id is None or change_request.submitted_by_id == getattr(user, 'pk', None):
        return
    pk = change_request.pk
    detail = str(_('approved by {who} ({have} of {need})').format(who=user, have=have, need=need))

    def send():
        current = ChangeRequest.objects.filter(pk=pk, status=StatusChoices.PENDING).select_related('submitted_by').first()
        if current is not None:
            notify([current.submitted_by], current, PARTLY_APPROVED, detail=detail, actor=user)
    transaction.on_commit(send)


def connect():
    register_event_types()
    post_save.connect(
        change_request_saved, sender=ChangeRequest,
        dispatch_uid='netbox_change_approval:notifications',
    )
