"""
Remove what would break NetBox pages once this plugin is gone. Run it while
the plugin is still installed, right before taking it out of PLUGINS.

NetBox's Notifications, Bookmarks and Subscriptions pages load the object of
every row. Once the plugin's models are gone, a row pointing at a change
request can't be loaded and the whole page fails with a server error. A
dashboard with the Pending Approvals widget can't be loaded either, so its
owner gets the default dashboard and an error message instead.

Change requests, rules and their history are left alone.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

APP_LABEL = 'netbox_change_approval'


def _plugin_widget_ids(dashboard):
    return [
        widget_id for widget_id, widget in dashboard.config.items()
        if isinstance(widget, dict) and str(widget.get('class', '')).startswith(f'{APP_LABEL}.')
    ]


class Command(BaseCommand):
    help = (
        'Delete notifications, bookmarks and subscriptions for change requests, and remove the '
        'Pending Approvals dashboard widget, so NetBox keeps working after the plugin is removed.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only show what would be removed.')
        parser.add_argument('--noinput', '--no-input', action='store_false', dest='interactive',
                            help='Do not ask for confirmation.')

    def handle(self, *args, dry_run=False, interactive=True, **options):
        from extras.models import Bookmark, Dashboard, Notification, Subscription

        from netbox_change_approval.models import ChangeRequest, StatusChoices

        rows = {
            'notifications': Notification.objects.filter(object_type__app_label=APP_LABEL),
            'bookmarks': Bookmark.objects.filter(object_type__app_label=APP_LABEL),
            'subscriptions': Subscription.objects.filter(object_type__app_label=APP_LABEL),
        }
        dashboards = [d for d in Dashboard.objects.all() if _plugin_widget_ids(d)]
        pending = ChangeRequest.objects.filter(status=StatusChoices.PENDING).count()

        for name, queryset in rows.items():
            self.stdout.write(f'{queryset.count()} {name} for change approval objects')
        self.stdout.write(f'{len(dashboards)} dashboards with the Pending Approvals widget')
        if pending:
            self.stdout.write(self.style.WARNING(
                f'{pending} change requests are still pending. Approve or reject them first: '
                'once the plugin is gone, pending edits and deletes are lost and pending new '
                'objects stay as normal objects.'
            ))

        if dry_run:
            return
        if interactive and input('Remove these? [y/N] ').strip().lower() not in ('y', 'yes'):
            self.stdout.write('Nothing removed.')
            return

        with transaction.atomic():
            for queryset in rows.values():
                queryset.delete()
            for dashboard in dashboards:
                for widget_id in _plugin_widget_ids(dashboard):
                    dashboard.delete_widget(widget_id)
                dashboard.save()
        self.stdout.write(self.style.SUCCESS(
            'Done. You can now take netbox_change_approval out of PLUGINS and restart NetBox.'
        ))
