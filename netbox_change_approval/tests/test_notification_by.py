from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from core.choices import ObjectChangeActionChoices
from core.models import ObjectChange
from dcim.models import Manufacturer
from extras.models import Notification
from users.models import User

from .. import notification_by
from ..models import ChangeRequest
from .test_approval_features import _Base


class NotificationByTests(_Base):
    """Each notification says who did it: "By: <user>" in the bell's
    drop-down and a "By" column on the Notifications page."""

    def _note(self, user, change_request):
        return Notification.objects.get(
            user=user, object_type=ContentType.objects.get_for_model(ChangeRequest), object_id=change_request.pk,
        )

    def test_bell_shows_submitter_of_new_request(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._update_request()
        self.client.force_login(self.alice)
        response = self.client.get('/extras/notifications/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change request needs your approval')
        self.assertContains(response, 'By: submitter')

    def test_decisions_name_the_decider(self):
        with self.captureOnCommitCallbacks(execute=True):
            change_request = self._update_request()
        with self.captureOnCommitCallbacks(execute=True):
            change_request.reject(self.bob, 'wrong rack')
        self.assertEqual(notification_by.by_name(self._note(self.submitter, change_request)), 'bob')

    def test_approval_names_the_approver(self):
        with self.captureOnCommitCallbacks(execute=True):
            change_request = self._update_request()
        with self.captureOnCommitCallbacks(execute=True):
            self._approve(self.carol, change_request)
        self.assertEqual(notification_by.by_name(self._note(self.submitter, change_request)), 'carol')

    def test_partial_approval_names_the_approver(self):
        self._rule(required_approvals=2)
        change_request = self._update_request()
        with self.captureOnCommitCallbacks(execute=True):
            self._approve(self.alice, change_request)
        self.assertEqual(notification_by.by_name(self._note(self.submitter, change_request)), 'alice')

    def test_comment_names_its_author(self):
        change_request = self._update_request()
        self.client.force_login(self.alice)
        url = reverse('plugins:netbox_change_approval:changerequest_comment', kwargs={'pk': change_request.pk})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {'text': 'why?'})
        self.assertEqual(notification_by.by_name(self._note(self.submitter, change_request)), 'alice')

    def test_netbox_notification_names_user_from_changelog(self):
        # An Event Rule or subscription notification ("Object updated").
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        content_type = ContentType.objects.get_for_model(Manufacturer)
        ObjectChange.objects.create(
            user=self.carol, user_name='carol', request_id='00000000-0000-0000-0000-000000000001',
            action=ObjectChangeActionChoices.ACTION_UPDATE, changed_object_type=content_type,
            changed_object_id=obj.pk, object_repr='Acme',
        )
        note = Notification.objects.create(
            user=self.alice, object_type=content_type, object_id=obj.pk, event_type='object_updated',
        )
        self.assertEqual(notification_by.by_name(note), 'carol')
        self.client.force_login(self.alice)
        self.assertContains(self.client.get('/extras/notifications/'), 'By: carol')

    def test_no_by_line_when_nobody_known(self):
        obj = Manufacturer.objects.create(name='Quiet', slug='quiet')
        Notification.objects.create(
            user=self.alice, object_type=ContentType.objects.get_for_model(Manufacturer), object_id=obj.pk,
            event_type='object_updated',
        )
        self.client.force_login(self.alice)
        response = self.client.get('/extras/notifications/')
        self.assertContains(response, 'Quiet')
        self.assertNotContains(response, 'By:')

    def test_notifications_page_has_by_column(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._update_request()
        self.client.force_login(self.alice)
        response = self.client.get(reverse('account:notifications'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '>By<')
        self.assertContains(response, 'submitter')
