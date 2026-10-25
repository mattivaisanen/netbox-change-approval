"""
Approve now, apply later.

An approver can give a time when approving ("Apply at", e.g. a maintenance
window). When that approval is the last one needed, the request is not
applied but scheduled: it stays pending, so the object stays locked, and a
background job applies it at that time, recorded as approved by whoever
scheduled it. Until then an approver can apply it at once, cancel the
schedule (back to an ordinary pending request, approvals kept) or reject it;
the submitter can still retract it.

If it can't be applied when due (the object changed meanwhile, say), the
schedule is dropped, the request stays pending, and the submitter and the
scheduler are told.

The job runs every five minutes in NetBox's background worker, the same one
that sends webhooks and runs scripts.
"""
import logging
import uuid

from django.db import transaction
from django.utils import timezone

from .models import ChangeRequest, StatusChoices

logger = logging.getLogger('netbox_change_approval.scheduling')


def _fake_request(user):
    """A stand-in web request, so NetBox writes the change log and runs event
    rules for a change applied in the background."""
    from django.utils.datastructures import MultiValueDict

    from utilities.request import NetBoxFakeRequest

    return NetBoxFakeRequest({
        'META': {}, 'COOKIES': {}, 'POST': {}, 'GET': {}, 'FILES': MultiValueDict(),
        'user': user, 'method': 'POST', 'path': '', 'path_info': '', 'id': uuid.uuid4(),
    })


def _members(change_request):
    if not change_request.batch_id:
        return [change_request]
    return list(ChangeRequest.objects.filter(
        batch_id=change_request.batch_id, status=StatusChoices.PENDING, scheduled_for__isnull=False,
    ).order_by('pk'))


def apply_scheduled(change_request):
    """Apply a scheduled request (and the rest of its batch) now. Returns
    True if applied, None if it was decided or unscheduled meanwhile (by a
    retract, say, or a second "Apply now"); on failure drops the schedule,
    notifies and returns False."""
    from netbox.context_managers import event_tracking

    from . import notifications

    user = change_request.scheduled_by
    members = _members(change_request)
    try:
        with event_tracking(_fake_request(user)):
            with transaction.atomic():
                # Lock and re-check: the request may have been retracted,
                # rejected or applied since it was read.
                locked = list(ChangeRequest.objects.select_for_update().filter(
                    pk__in=[m.pk for m in members], status=StatusChoices.PENDING, scheduled_for__isnull=False,
                ).order_by('pk'))
                if len(locked) != len(members):
                    return None
                for member in locked:
                    member.apply(user=user)
        return True
    except Exception as error:
        logger.warning('Scheduled change request #%s could not be applied: %s', change_request.pk, error)
        # scheduled_by stays: the notification's "By" line names it.
        ChangeRequest.objects.filter(pk__in=[m.pk for m in members]).update(scheduled_for=None)
        notifications.schedule_failed(change_request, user, str(error))
        return False


def apply_due(now=None):
    """Apply every scheduled request whose time has come. Returns how many
    were applied."""
    now = now or timezone.now()
    count, done = 0, set()
    due = ChangeRequest.objects.filter(
        status=StatusChoices.PENDING, scheduled_for__isnull=False, scheduled_for__lte=now,
    ).select_related('scheduled_by').order_by('scheduled_for', 'pk')
    for change_request in due:
        if change_request.pk in done:
            continue
        members = _members(change_request)
        done.update(m.pk for m in members)
        if apply_scheduled(change_request):
            count += len(members)
    return count


def cancel(change_request):
    """Back to an ordinary pending request (approvals kept)."""
    ChangeRequest.objects.filter(pk__in=[m.pk for m in _members(change_request)]).update(scheduled_for=None)


try:
    from netbox.jobs import JobRunner, system_job
except ImportError:  # NetBox without system jobs
    JobRunner = system_job = None

if system_job is not None:
    @system_job(interval=5)
    class ApplyScheduledChangesJob(JobRunner):
        class Meta:
            name = 'Apply scheduled change requests'

        def run(self, *args, **kwargs):
            count = apply_due()
            self.logger.info(f'Applied {count} scheduled change request(s).')
