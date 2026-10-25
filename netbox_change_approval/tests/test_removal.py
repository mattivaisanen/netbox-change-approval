"""
What core NetBox looks like once this plugin is removed, with and without
running `prepare_uninstall` first.

The plugin can't really be uninstalled inside a test run, so removal is
simulated: rows this plugin leaves behind are created while it is installed,
then the plugin is made to look gone (its models can't be loaded through
their content types any more, its widget, event types, notification patches,
template extensions and middleware are taken away) and core pages are loaded
as the user those rows belong to.
"""
import uuid
from contextlib import ExitStack, contextmanager
from io import StringIO
from unittest import mock

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.shortcuts import render as django_render
from django.test import TestCase, override_settings

from core.models import Job, ObjectChange, ObjectType
from dcim.models import Site
from extras.models import (
    Bookmark, CustomField, CustomLink, Dashboard, EventRule, ExportTemplate, JournalEntry, Notification, SavedFilter,
    Subscription, Tag, Webhook,
)
from netbox.registry import registry
from users.models import ObjectPermission, User

from netbox_change_approval.models import ChangeRequest, WorkTask

APP_LABEL = 'netbox_change_approval'

CORE_PAGES = [
    '/',
    '/user/notifications/',
    '/user/bookmarks/',
    '/user/subscriptions/',
    '/extras/notifications/',
    '/core/changelog/',
    '/extras/event-rules/',
    '/extras/tags/',
    '/extras/journal-entries/',
    '/extras/custom-fields/',
    '/extras/custom-links/',
    '/extras/export-templates/',
    '/extras/saved-filters/',
    '/core/jobs/',
    '/users/permissions/',
    '/api/extras/notifications/',
    '/api/extras/bookmarks/',
    '/api/extras/subscriptions/',
    '/api/extras/journal-entries/',
    '/api/extras/event-rules/',
    '/api/extras/tags/',
    '/api/extras/custom-fields/',
    '/api/core/object-changes/',
    '/api/core/object-types/',
    '/api/users/permissions/',
]


@contextmanager
def plugin_gone():
    """Make NetBox behave, as far as a test can, as if this plugin were not installed."""
    from extras import views as extras_views
    from extras.tables import NotificationTable

    real_model_class = ContentType.model_class

    def model_class(self):
        if self.app_label == APP_LABEL:
            return None
        return real_model_class(self)

    base_columns = {k: v for k, v in NotificationTable.base_columns.items() if k != 'by'}
    meta = NotificationTable.Meta
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(ContentType, 'model_class', model_class))
        stack.enter_context(mock.patch.dict(registry['widgets'], {}))
        for key in [k for k in registry['widgets'] if k.startswith(f'{APP_LABEL}.')]:
            del registry['widgets'][key]
        stack.enter_context(mock.patch.dict(registry['event_types'], {}))
        for key in [k for k in registry['event_types'] if k.startswith(f'{APP_LABEL}.')]:
            del registry['event_types'][key]
        stack.enter_context(mock.patch.object(extras_views, 'render', django_render))
        stack.enter_context(mock.patch.object(NotificationTable, 'base_columns', base_columns))
        stack.enter_context(mock.patch.object(meta, 'fields', tuple(f for f in meta.fields if f != 'by')))
        stack.enter_context(mock.patch.object(
            meta, 'default_columns', tuple(f for f in meta.default_columns if f != 'by'),
        ))
        stack.enter_context(mock.patch.dict(registry['plugins']['template_extensions'], {}, clear=True))
        stack.enter_context(override_settings(
            MIDDLEWARE=[m for m in settings.MIDDLEWARE if not m.startswith(f'{APP_LABEL}.')],
        ))
        yield


class RemovalLeftoversTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='alice', is_superuser=True)
        cls.site = Site.objects.create(name='Removal S1', slug='removal-s1')
        site_type = ContentType.objects.get_for_model(Site)
        cls.request = ChangeRequest.objects.create(
            content_type=site_type, object_id=cls.site.pk, action='update', status='applied',
            object_repr='Removal S1', submitted_by=cls.user,
        )
        request_ct = ContentType.objects.get_for_model(ChangeRequest)
        request_ot = ObjectType.objects.get_for_model(ChangeRequest)
        cls.request_ct = request_ct
        pk = cls.request.pk

        Notification.objects.create(user=cls.user, object_type=request_ct, object_id=pk,
                                    event_type='netbox_change_approval.approved')
        Bookmark.objects.create(user=cls.user, object_type=request_ct, object_id=pk)
        Subscription.objects.create(user=cls.user, object_type=request_ct, object_id=pk)
        Dashboard.objects.create(user=cls.user, layout=[{'id': 'w1'}], config={
            'w1': {'class': 'netbox_change_approval.ApprovalsWidget'},
        })
        cls.change = ObjectChange.objects.create(
            changed_object_type=request_ct, changed_object_id=pk, object_repr='#1', action='update',
            user=cls.user, user_name='alice', request_id=uuid.uuid4(), postchange_data={'status': 'applied'},
        )
        cls.journal = JournalEntry.objects.create(
            assigned_object_type=request_ct, assigned_object_id=pk, created_by=cls.user, kind='info',
            comments='note',
        )
        cls.tag = Tag.objects.create(name='Removal tag', slug='removal-tag')
        cls.request.tags.add(cls.tag)
        webhook = Webhook.objects.create(name='Removal hook', payload_url='http://example.invalid/')
        cls.rule = EventRule.objects.create(
            name='Removal rule', event_types=['object_updated'], action_type='webhook',
            action_object_type=ContentType.objects.get_for_model(Webhook), action_object_id=webhook.pk,
        )
        cls.rule.object_types.set([request_ot])
        cls.field = CustomField.objects.create(name='removal_cf', type='text')
        cls.field.object_types.set([request_ot])
        link = CustomLink.objects.create(name='Removal link', link_text='x', link_url='http://example.invalid/')
        link.object_types.set([request_ot])
        template = ExportTemplate.objects.create(name='Removal export', template_code='x')
        template.object_types.set([request_ot])
        saved = SavedFilter.objects.create(name='Removal filter', slug='removal-filter', parameters={'q': 'x'})
        saved.object_types.set([request_ot])
        cls.permission = ObjectPermission.objects.create(name='Removal perm', actions=['view'])
        cls.permission.object_types.set([request_ot])
        site_ot = ObjectType.objects.get_for_model(Site)
        cls.mixed_field = CustomField.objects.create(name='removal_cf_mixed', type='text')
        cls.mixed_field.object_types.set([request_ot, site_ot])
        cls.mixed_permission = ObjectPermission.objects.create(name='Removal mixed perm', actions=['view'])
        cls.mixed_permission.object_types.set([request_ot, site_ot])
        cls.site_change = ObjectChange.objects.create(
            changed_object_type=site_type, changed_object_id=cls.site.pk, object_repr='Removal S1',
            action='update', user=cls.user, user_name='alice', request_id=uuid.uuid4(),
            postchange_data={'name': 'Removal S1'},
        )
        # An object custom field on sites whose value is a change request.
        cls.object_field = CustomField.objects.create(
            name='removal_request', type='object', related_object_type=request_ot,
        )
        cls.object_field.object_types.set([site_ot])
        Site.objects.filter(pk=cls.site.pk).update(custom_field_data={'removal_request': pk})
        cls.event_rule = EventRule.objects.create(
            name='Removal events', event_types=['object_created', 'netbox_change_approval.approved'],
            action_type='webhook', action_object_type=ContentType.objects.get_for_model(Webhook),
            action_object_id=webhook.pk,
        )
        cls.event_rule.object_types.set([site_ot])
        from netbox_change_approval.scheduling import ApplyScheduledChangesJob
        cls.job = Job.objects.create(name=ApplyScheduledChangesJob.name, status='scheduled', job_id=uuid.uuid4())
        cls.user.config.set('tables.NotificationTable.columns', ['object', 'by', 'created'], commit=True)
        WorkTask.objects.create(change_request=cls.request, site=cls.site, text='Removal task')

    def _broken_pages(self):
        self.client.force_login(self.user)
        pages = CORE_PAGES + [
            f'/core/changelog/{self.site_change.pk}/',
            f'/extras/tags/{self.tag.pk}/',
            f'/extras/custom-fields/{self.mixed_field.pk}/',
            f'/users/permissions/{self.mixed_permission.pk}/',
            f'/dcim/sites/{self.site.pk}/',
            f'/api/dcim/sites/{self.site.pk}/',
        ]
        broken = []
        with plugin_gone():
            for url in pages:
                accept = 'application/json' if url.startswith('/api/') else 'text/html'
                try:
                    response = self.client.get(url, HTTP_ACCEPT=accept)
                except Exception as exc:
                    broken.append(f'{url}: {type(exc).__name__}: {str(exc)[:200]}')
                    continue
                context = getattr(response, 'context', None)
                messages = [str(m) for m in context['messages']] if context and 'messages' in context else []
                errors = [m for m in messages if 'error' in m.lower()]
                if response.status_code >= 400 or errors:
                    broken.append(f'{url}: {response.status_code} {errors}')
        return broken

    def test_pages_break_without_prepare_uninstall(self):
        """Documents why prepare_uninstall exists: without it, these core
        pages fail once the plugin is gone."""
        broken = self._broken_pages()
        for url in ('/', '/user/notifications/', '/user/bookmarks/', '/user/subscriptions/', '/core/changelog/',
                    '/extras/journal-entries/', '/api/core/object-changes/', f'/extras/tags/{self.tag.pk}/',
                    f'/extras/custom-fields/{self.mixed_field.pk}/'):
            self.assertTrue(any(line.startswith(f'{url}:') for line in broken), f'{url} expected broken: {broken}')

    def test_pages_work_after_prepare_uninstall(self):
        call_command('prepare_uninstall', '--noinput', stdout=StringIO())
        self.assertEqual(self._broken_pages(), [])

    def test_prepare_uninstall_keeps_core_objects_also_used_elsewhere(self):
        call_command('prepare_uninstall', '--noinput', stdout=StringIO())
        site_type = ObjectType.objects.get_for_model(Site)
        self.assertEqual([o.pk for o in self.mixed_field.object_types.all()], [site_type.pk])
        self.assertEqual([o.pk for o in self.mixed_permission.object_types.all()], [site_type.pk])
        self.assertFalse(CustomField.objects.filter(pk=self.field.pk).exists())
        self.assertFalse(ObjectPermission.objects.filter(pk=self.permission.pk).exists())
        self.assertFalse(EventRule.objects.filter(pk=self.rule.pk).exists())
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())
        self.assertFalse(ObjectChange.objects.filter(pk=self.change.pk).exists())
        self.assertTrue(ObjectChange.objects.filter(pk=self.site_change.pk).exists())
        self.assertTrue(ChangeRequest.objects.filter(pk=self.request.pk).exists())
        self.assertFalse(CustomField.objects.filter(pk=self.object_field.pk).exists())
        self.event_rule.refresh_from_db()
        self.assertEqual(self.event_rule.event_types, ['object_created'])
        self.assertFalse(Job.objects.filter(pk=self.job.pk).exists())
