"""
Remove what would break NetBox pages once this plugin is gone. Run it while
the plugin is still installed, right before taking it out of PLUGINS.

Many NetBox pages load the object behind each row they list. Once the
plugin's models are gone, a row pointing at a change request (or a rule, a
comment, ...) can't be loaded and the whole page fails with a server error:
Notifications, Bookmarks, Subscriptions, Changelog, Journal Entries, and the
REST API lists behind them. A tag used on a change request breaks that tag's
page, and a custom field offered on change requests breaks that field's page.
A dashboard with the Pending Approvals widget can't be loaded either, so its
owner gets the default dashboard and an error message instead.

So this deletes those rows, takes the plugin's object types off custom
fields, custom links, export templates, saved filters, tags, event rules and
permissions (deleting the ones that were only about this plugin), and
removes the widget from dashboards.

Change requests, rules and the plugin's own tables are left alone.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

APP_LABEL = 'netbox_change_approval'


def _plugin_widget_ids(dashboard):
    return [
        widget_id for widget_id, widget in dashboard.config.items()
        if isinstance(widget, dict) and str(widget.get('class', '')).startswith(f'{APP_LABEL}.')
    ]


def _rows():
    """Rows in NetBox's own tables that point at this plugin's objects."""
    from core.models import ObjectChange
    from extras.models import Bookmark, ImageAttachment, JournalEntry, Notification, Subscription, TableConfig, \
        TaggedItem

    ours = {'object_type__app_label': APP_LABEL}
    return {
        'notifications': Notification.objects.filter(**ours),
        'bookmarks': Bookmark.objects.filter(**ours),
        'subscriptions': Subscription.objects.filter(**ours),
        'changelog entries': ObjectChange.objects.filter(
            Q(changed_object_type__app_label=APP_LABEL) | Q(related_object_type__app_label=APP_LABEL)
        ),
        'journal entries': JournalEntry.objects.filter(assigned_object_type__app_label=APP_LABEL),
        'tag assignments': TaggedItem.objects.filter(content_type__app_label=APP_LABEL),
        'image attachments': ImageAttachment.objects.filter(**ours),
        'saved table configs': TableConfig.objects.filter(**ours),
    }


def _object_type_users():
    """NetBox objects that can be limited to object types, offered on any of
    this plugin's. Each gets this plugin's types removed; the ones left with
    no object type at all were only about this plugin and are deleted."""
    from extras.models import CustomField, CustomLink, EventRule, ExportTemplate, SavedFilter, Tag
    from users.models import ObjectPermission

    found = {}
    for label, model in (
        ('custom fields', CustomField), ('custom links', CustomLink), ('export templates', ExportTemplate),
        ('saved filters', SavedFilter), ('tags', Tag), ('event rules', EventRule), ('permissions', ObjectPermission),
    ):
        objects = list(model.objects.filter(object_types__app_label=APP_LABEL).distinct())
        if objects:
            found[label] = objects
    return found


def _only_ours(obj):
    return not obj.object_types.exclude(app_label=APP_LABEL).exists()


class Command(BaseCommand):
    help = (
        "Delete NetBox rows that point at this plugin's objects (notifications, bookmarks, subscriptions, "
        'changelog and journal entries, tag assignments), take its object types off custom fields, tags, '
        'event rules, permissions and the like, and remove the Pending Approvals dashboard widget, so NetBox '
        'keeps working after the plugin is removed.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only show what would be removed.')
        parser.add_argument('--noinput', '--no-input', action='store_false', dest='interactive',
                            help='Do not ask for confirmation.')

    def handle(self, *args, dry_run=False, interactive=True, **options):
        from extras.models import Dashboard
        from netbox_change_approval.models import ChangeRequest, StatusChoices

        rows = _rows()
        users = _object_type_users()
        dashboards = [d for d in Dashboard.objects.all() if _plugin_widget_ids(d)]
        pending = ChangeRequest.objects.filter(status=StatusChoices.PENDING).count()

        for name, queryset in rows.items():
            count = queryset.count()
            if count:
                self.stdout.write(f'{count} {name} for change approval objects')
        for label, objects in users.items():
            deleted = [str(o) for o in objects if _only_ours(o)]
            kept = [str(o) for o in objects if not _only_ours(o)]
            if kept:
                self.stdout.write(f'{label} to take change approval object types off: {", ".join(kept)}')
            if deleted:
                self.stdout.write(f'{label} only for change approval objects, to delete: {", ".join(deleted)}')
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
            for objects in users.values():
                for obj in objects:
                    if _only_ours(obj):
                        obj.delete()
                    else:
                        obj.object_types.remove(*obj.object_types.filter(app_label=APP_LABEL))
            for dashboard in dashboards:
                for widget_id in _plugin_widget_ids(dashboard):
                    dashboard.delete_widget(widget_id)
                dashboard.save()
        self.stdout.write(self.style.SUCCESS(
            'Done. You can now take netbox_change_approval out of PLUGINS and restart NetBox.'
        ))
