"""
Delete old, finished change requests (applied, rejected, retracted,
expired), with their approvals, comments and field work. Pending and
sent-back requests are kept, and so are applied ones with field work still
open. Notifications, bookmarks, journal entries and tags on the deleted
requests go with them. The changes themselves stay in NetBox's changelog.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from netbox_change_approval.models import ChangeRequest, StatusChoices, WorkTask

FINISHED = (StatusChoices.APPLIED, StatusChoices.REJECTED, StatusChoices.RETRACTED, StatusChoices.EXPIRED)


def prunable(days, now=None):
    """Finished requests whose end is more than `days` days ago."""
    cutoff = (now or timezone.now()) - timedelta(days=days)
    open_work = WorkTask.objects.filter(done_at__isnull=True).values('change_request_id')
    return ChangeRequest.objects.annotate(
        ended=Coalesce('decided_at', 'applied_at', 'submitted_at'),
    ).filter(status__in=FINISHED, ended__lt=cutoff).exclude(pk__in=open_work)


class Command(BaseCommand):
    help = 'Delete finished change requests older than --older-than days (pending and sent-back ones are kept).'

    def add_arguments(self, parser):
        parser.add_argument('--older-than', type=int, required=True, metavar='DAYS',
                            help='Delete requests that ended more than this many days ago.')
        parser.add_argument('--dry-run', action='store_true', help='Only show how many would be deleted.')
        parser.add_argument('--noinput', '--no-input', action='store_false', dest='interactive',
                            help='Do not ask for confirmation.')

    def handle(self, *args, older_than, dry_run=False, interactive=True, **options):
        if older_than < 1:
            raise CommandError('--older-than must be at least 1 day.')
        queryset = prunable(older_than)
        count = queryset.count()
        self.stdout.write(f'{count} change request(s) ended more than {older_than} days ago.')
        if not count or dry_run:
            return
        if interactive and input('Delete them? [y/N] ').strip().lower() not in ('y', 'yes'):
            self.stdout.write('Nothing deleted.')
            return
        with transaction.atomic():
            ChangeRequest.objects.filter(pk__in=list(queryset.values_list('pk', flat=True))).delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} change request(s).'))
