from django.core.management.base import BaseCommand

from netbox_change_approval.expiry import expire_overdue


class Command(BaseCommand):
    help = 'Expire pending change requests that are past their deadline (see the "Expire after" rule setting).'

    def handle(self, *args, **options):
        count = expire_overdue()
        self.stdout.write(f'Expired {count} change request(s).')
