"""
Expiring requests nobody decided in time.

A request's deadline (ChangeRequest.expires_at) comes from its approval rule's
"Expire after (days)", or the plugin setting `expire_pending_after_days` when
the rule leaves it blank (off unless set). Resubmitting after a send-back sets
a new deadline.

An expired request ends like a rejection: nothing is applied, a pending
create's object is removed, the object is unlocked, and the submitter sees it
in their inbox and gets a notification. A bulk batch expires together.

Checked hourly by a NetBox background job (needs NetBox's worker running, as
for scripts and reports), or on demand with
`manage.py expire_change_requests`.
"""
import logging

from django.db import transaction
from django.utils import timezone

from .models import ChangeRequest, StatusChoices

logger = logging.getLogger('netbox_change_approval.expiry')


def expire_overdue(now=None):
    """Expire every pending request past its deadline. Returns how many."""
    now = now or timezone.now()
    count, done = 0, set()
    overdue = ChangeRequest.objects.filter(
        status=StatusChoices.PENDING, expires_at__lte=now, scheduled_for__isnull=True,
    ).order_by('pk')
    for change_request in overdue:
        if change_request.pk in done:
            continue
        members = [change_request]
        if change_request.batch_id:
            members = list(ChangeRequest.objects.filter(
                batch_id=change_request.batch_id, status=StatusChoices.PENDING,
            ).order_by('-pk'))  # newest first: a device goes before its new rack
        try:
            with transaction.atomic():
                for member in members:
                    member.expire()
        except Exception:
            logger.exception('Could not expire change request #%s', change_request.pk)
            continue
        done.update(m.pk for m in members)
        count += len(members)
    return count


try:
    from netbox.jobs import JobRunner, system_job
except ImportError:  # NetBox without system jobs: use the management command
    JobRunner = system_job = None

if system_job is not None:
    @system_job(interval=60)
    class ExpireChangeRequestsJob(JobRunner):
        class Meta:
            name = 'Expire undecided change requests'

        def run(self, *args, **kwargs):
            count = expire_overdue()
            self.logger.info(f'Expired {count} change request(s).')
