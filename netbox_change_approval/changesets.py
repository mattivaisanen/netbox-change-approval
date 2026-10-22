"""
Change sets: collect several changes, submit them as one.

Start a change set ("Install sw1 in rack A3", optional ticket) on the Change
Sets page. While it is active, a banner shows on every page, and every change
of yours that needs approval goes into it instead of standing alone: they
share its batch_id, the same way a bulk edit's requests do, so they are
approved or rejected together, show as one item in inboxes, and notify once.

A draft is yours alone: approvers don't see it in their queue or inbox, can't
decide it, and aren't notified. "Submit" sends the whole set to them;
"Discard" withdraws every change in it; "Leave" stops adding to it (you can
come back to it later).

All of it lives in this module, ChangeSet (models.py), migration 0012, the
changeset*.html templates and the Change Sets menu item, with small hooks in
permissions.py and notifications.py. PLUGINS_CONFIG['netbox_change_approval']
['change_sets'] = False turns it off.
"""
from django.db.models.signals import pre_save
from django.utils import timezone

from .models import ChangeRequest, ChangeSet, StatusChoices

SESSION_KEY = 'nbca_change_set'


def enabled():
    try:
        from .plugin_settings import get_setting
    except ImportError:
        from netbox.plugins.utils import get_plugin_config

        return get_plugin_config('netbox_change_approval', 'change_sets') is not False
    return get_setting('change_sets') is not False


def active_for(request):
    """The user's active draft change set for this browser session, or None."""
    if not enabled() or request is None:
        return None
    user = getattr(request, 'user', None)
    session = getattr(request, 'session', None)
    if user is None or not user.is_authenticated or session is None:
        return None
    try:
        pk = session.get(SESSION_KEY)
    except Exception:
        return None
    if not pk:
        return None
    return ChangeSet.objects.filter(pk=pk, created_by=user, status=ChangeSet.DRAFT).first()


def activate(request, change_set):
    request.session[SESSION_KEY] = change_set.pk


def leave(request):
    request.session.pop(SESSION_KEY, None)


def draft_batch_ids():
    if not enabled():
        return []
    return list(ChangeSet.objects.filter(status=ChangeSet.DRAFT).values_list('batch_id', flat=True))


def is_draft(change_request):
    return bool(change_request.batch_id) and enabled() and ChangeSet.objects.filter(
        batch_id=change_request.batch_id, status=ChangeSet.DRAFT,
    ).exists()


def for_request(change_request):
    if not change_request.batch_id or not enabled():
        return None
    return ChangeSet.objects.filter(batch_id=change_request.batch_id).first()


def _join(sender, instance, **kwargs):
    """A new request of yours joins your active change set."""
    if not instance._state.adding or instance.status != StatusChoices.PENDING or instance.auto_approved:
        return
    from netbox.context import current_request

    change_set = active_for(current_request.get())
    if change_set is not None and instance.submitted_by_id == change_set.created_by_id:
        instance.batch_id = change_set.batch_id


def submit(change_set):
    from . import notifications

    change_set.status = ChangeSet.SUBMITTED
    change_set.submitted_at = timezone.now()
    change_set.save(update_fields=['status', 'submitted_at'])
    pending = change_set.requests().filter(status=StatusChoices.PENDING).order_by('pk')
    # One "needs approval" notification per approver group, on its first request.
    seen = set()
    for change_request in pending:
        if change_request.approver_group_id in seen:
            continue
        seen.add(change_request.approver_group_id)
        notifications.request_updated(change_request)
    return pending.count()


def discard(change_set, user):
    count = 0
    for change_request in change_set.requests().filter(status=StatusChoices.PENDING):
        change_request.retract(user)
        count += 1
    change_set.delete()
    return count


def connect():
    pre_save.connect(_join, sender=ChangeRequest, dispatch_uid='netbox_change_approval:changesets')
