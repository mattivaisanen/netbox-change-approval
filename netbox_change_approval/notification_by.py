"""
"By: <user>" on notifications: who did the thing a notification is about,
shown in the bell's drop-down (a line under "<event> 3 hours ago") and as a
"By" column on the Notifications page. For every notification, not just
this plugin's: an Event Rule or a subscription notification ("Object
created", "Object updated") names the user of the change behind it.

NetBox doesn't store who caused a notification, so it is worked out when
shown:

- This plugin's notifications, from the change request: its submitter for
  "needs approval", whoever decided it for approved/rejected/sent back, the
  comment's author, the latest approver, whoever scheduled it. Nobody for
  "expired" (nobody did anything).
- Any other notification: the user of the object's latest changelog entry
  made before the notification.

NetBox has no hook for either place, so this replaces the drop-down's
template (a copy of NetBox's htmx/notifications.html with one line added,
kept in templates/netbox_change_approval/inc/notifications.html) and adds a
column to NetBox's NotificationTable. See patch().
"""
import logging

from django.utils.translation import gettext_lazy as _

logger = logging.getLogger('netbox_change_approval.notification_by')

NETBOX_TEMPLATE = 'htmx/notifications.html'
OUR_TEMPLATE = 'netbox_change_approval/inc/notifications.html'


def _username(user):
    return user.username if user is not None else None


def _from_change_request(notification, change_request):
    from . import notifications as n

    event = notification.event_type
    if event == n.NEEDS_APPROVAL:
        return _username(change_request.submitted_by)
    if event in (n.APPROVED, n.REJECTED, n.SENT_BACK):
        return _username(change_request.decided_by)
    if event == n.COMMENTED:
        comment = change_request.comments.filter(created__lte=notification.created).select_related(
            'author').order_by('-created', '-pk').first()
        return _username(comment.author) if comment is not None else None
    if event == n.PARTLY_APPROVED:
        approval = change_request.approvals.filter(created__lte=notification.created).select_related(
            'user').order_by('-created', '-pk').first()
        return _username(approval.user) if approval is not None else None
    if event in (n.SCHEDULED, n.SCHEDULE_FAILED):
        return _username(change_request.scheduled_by)
    if event == n.EXPIRED:
        return None
    return False  # not ours: look in the changelog


def _from_changelog(notification):
    from core.models import ObjectChange

    change = ObjectChange.objects.filter(
        changed_object_type_id=notification.object_type_id,
        changed_object_id=notification.object_id,
        time__lte=notification.created,
    ).order_by('-time', '-pk').only('user_name').first()
    return change.user_name if change is not None and change.user_name else None


def by_name(notification):
    """The username of whoever the notification is about, or None."""
    from .models import ChangeRequest

    try:
        if notification.object_type.model_class() is ChangeRequest:
            change_request = ChangeRequest.objects.filter(pk=notification.object_id).select_related(
                'submitted_by', 'decided_by', 'scheduled_by',
            ).first()
            if change_request is not None:
                name = _from_change_request(notification, change_request)
                if name is not False:
                    return name
                # A NetBox event on the request itself ("Object created"):
                # the changelog, else whoever submitted it.
                return _from_changelog(notification) or _username(change_request.submitted_by)
        return _from_changelog(notification)
    except Exception:
        logger.exception('Could not tell who caused notification #%s', notification.pk)
        return None


def annotate(notifications):
    """`notifications` as a list, each with `nbca_by` set."""
    notifications = list(notifications)
    for notification in notifications:
        notification.nbca_by = by_name(notification)
    return notifications


def _render(original):
    def render(request, template_name, context=None, *args, **kwargs):
        if template_name == NETBOX_TEMPLATE and context and 'notifications' in context:
            context = {**context, 'notifications': annotate(context['notifications'])}
            template_name = OUR_TEMPLATE
        return original(request, template_name, context, *args, **kwargs)
    render._nbca_patched = True
    return render


def _by_column():
    import django_tables2 as tables

    class ByColumn(tables.Column):
        def __init__(self):
            super().__init__(
                verbose_name=_('By'), accessor=tables.A('pk'), orderable=False, empty_values=(),
            )

        def render(self, record):
            return by_name(record) or '—'

        def value(self, record):
            return by_name(record) or ''

    return ByColumn()


def patch():
    """Point NetBox's notification views at our drop-down template and add
    the "By" column to the Notifications table. Safe to call twice."""
    from extras import views
    from extras.tables import NotificationTable

    if not getattr(views.render, '_nbca_patched', False):
        views.render = _render(views.render)

    if 'by' not in NotificationTable.base_columns:
        NotificationTable.base_columns['by'] = _by_column()
        meta = NotificationTable.Meta
        meta.fields = (*meta.fields, 'by')
        default = list(getattr(meta, 'default_columns', meta.fields))
        if 'by' not in default:
            # After "Event Type", before "Created": reads "Object updated by bob".
            at = default.index('created') if 'created' in default else len(default)
            default.insert(at, 'by')
        meta.default_columns = tuple(default)
