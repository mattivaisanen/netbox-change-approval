from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from dcim.models import Site
from extras.models import Bookmark, Dashboard, Notification
from users.models import User

from netbox_change_approval.models import ChangeRequest


class PrepareUninstallTest(TestCase):

    def test_removes_rows_and_widget_that_would_break_pages(self):
        user = User.objects.create_user(username='alice')
        site = Site.objects.create(name='S1', slug='s1')
        site_type = ContentType.objects.get_for_model(Site)
        request = ChangeRequest.objects.create(
            content_type=site_type, object_id=site.pk, action='update', status='applied',
            object_repr='S1', submitted_by=user,
        )
        request_type = ContentType.objects.get_for_model(ChangeRequest)
        Notification.objects.create(user=user, object_type=request_type, object_id=request.pk,
                                    event_type='object_updated')
        Notification.objects.create(user=user, object_type=site_type, object_id=site.pk,
                                    event_type='object_updated')
        Bookmark.objects.create(user=user, object_type=request_type, object_id=request.pk)
        dashboard = Dashboard.objects.create(user=user, layout=[{'id': 'w1'}, {'id': 'w2'}], config={
            'w1': {'class': 'netbox_change_approval.ApprovalsWidget'},
            'w2': {'class': 'extras.NoteWidget'},
        })

        call_command('prepare_uninstall', '--noinput', stdout=StringIO())

        self.assertEqual(list(Notification.objects.values_list('object_type', flat=True)), [site_type.pk])
        self.assertFalse(Bookmark.objects.exists())
        dashboard.refresh_from_db()
        self.assertEqual(list(dashboard.config), ['w2'])
        self.assertEqual(dashboard.layout, [{'id': 'w2'}])
        self.assertTrue(ChangeRequest.objects.filter(pk=request.pk).exists())
