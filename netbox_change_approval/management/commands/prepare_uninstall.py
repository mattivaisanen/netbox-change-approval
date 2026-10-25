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
permissions (deleting the ones that were only about this plugin), deletes
custom fields whose values are this plugin's objects, takes the plugin's
events off event rules, removes the widget from dashboards and cancels the
plugin's queued background jobs.

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


def _object_fields():
    """Custom fields of type object/multiple objects that point at this
    plugin's objects: their values can't be shown once it is gone, and they
    keep NetBox from removing the plugin's object types."""
    from extras.models import CustomField

    return list(CustomField.objects.filter(related_object_type__app_label=APP_LABEL))


def _event_rules_with_our_events():
    """Event rules that fire on this plugin's own events ("Change request
    approved", ...). Those event types are gone with the plugin."""
    from extras.models import EventRule

    return [rule for rule in EventRule.objects.all() if any(_is_ours(name) for name in rule.event_types)]


def _is_ours(event_type):
    return str(event_type).startswith(f'{APP_LABEL}.')


def _queued_jobs():
    """This plugin's background jobs still waiting to run (expiry, scheduled
    apply). Left queued, the worker would try to run code that is gone."""
    from core.choices import JobStatusChoices
    from core.models import Job
    from netbox.registry import registry

    names = {
        getattr(job_class, 'name', None) for job_class in registry.get('system_jobs', {})
        if job_class.__module__.startswith(f'{APP_LABEL}.')
    } - {None}
    return list(Job.objects.filter(
        Q(object_type__app_label=APP_LABEL) | Q(name__in=names),
        status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
    ))


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
        object_fields = _object_fields()
        if object_fields:
            self.stdout.write(
                f'custom fields pointing at change approval objects, to delete: '
                f'{", ".join(str(f) for f in object_fields)}'
            )
        event_rules = _event_rules_with_our_events()
        if event_rules:
            self.stdout.write(
                f'event rules to take change approval events off: {", ".join(str(r) for r in event_rules)}'
            )
        jobs = _queued_jobs()
        if jobs:
            self.stdout.write(f'{len(jobs)} queued change approval background jobs')
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
            for field in object_fields:
                field.delete()
            for rule in event_rules:
                rule = type(rule).objects.filter(pk=rule.pk).first()  # may be deleted above
                if rule is None:
                    continue
                rule.event_types = [name for name in rule.event_types if not _is_ours(name)]
                if rule.event_types:
                    rule.save()
                else:
                    rule.delete()
            for dashboard in dashboards:
                for widget_id in _plugin_widget_ids(dashboard):
                    dashboard.delete_widget(widget_id)
                dashboard.save()
        for job in jobs:
            job.delete()  # also takes it off the worker's queue; not undone by a rollback, so last
        self.stdout.write(self.style.SUCCESS(
            'Done. You can now take netbox_change_approval out of PLUGINS and restart NetBox.'
        ))
