"""On/off switches for optional features (features.py)."""
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from dcim.models import Manufacturer
from extras.models import Notification
from users.models import Group, User

from .. import features, gating, permissions, plugin_settings
from ..models import ActionChoices, ApproverAbsence, ChangeRequest, ChangeRequestComment, StatusChoices
from .utils import fake_request


class FeatureSwitchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers, _ = Group.objects.get_or_create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.bob = User.objects.create(username='bob')
        cls.submitter = User.objects.create(username='submitter')

    def _request(self, name='Vendor'):
        obj = Manufacturer.objects.create(name=name, slug=name.lower())
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'changed'
        return gating.gate(obj, ActionChoices.UPDATE, fake_request(self.submitter))

    def _off(self, key):
        plugin_settings.save(key, False)
        self.assertFalse(features.on(key))

    def _url(self, name, change_request):
        return reverse(f'plugins:netbox_change_approval:changerequest_{name}', kwargs={'pk': change_request.pk})

    def test_all_on_by_default(self):
        for key in features.ALL:
            self.assertTrue(features.on(key), key)
            self.assertIn(key, plugin_settings.BY_KEY)

    def test_scheduled_approvals_off(self):
        change_request = self._request()
        self._off(features.SCHEDULED_APPROVALS)
        self.client.force_login(self.approver)
        page = self.client.get(self._url('approve', change_request))
        self.assertNotContains(page, 'name="apply_at"')

        # A page opened before it was turned off: nothing happens.
        when = timezone.localtime(timezone.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(self._url('approve', change_request), {'confirm': True, 'apply_at': when})
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertIsNone(change_request.scheduled_for)
        self.assertFalse(change_request.current_approvals().exists())

        self.client.post(self._url('approve', change_request), {'confirm': True})
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)

    def test_send_back_off(self):
        change_request = self._request()
        self.client.force_login(self.approver)
        self.assertContains(self.client.get(change_request.get_absolute_url()), self._url('sendback', change_request))
        self._off(features.SEND_BACK)
        page = self.client.get(change_request.get_absolute_url())
        self.assertNotContains(page, self._url('sendback', change_request))
        self.client.post(self._url('sendback', change_request), {'confirm': True, 'comment': 'fix it'})
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertNotContains(
            self.client.get(reverse('plugins:netbox_change_approval:changerequest_inbox')),
            self._url('sendback', change_request),
        )

    def test_comments_off(self):
        change_request = self._request()
        ChangeRequestComment.objects.create(change_request=change_request, author=self.approver, text='earlier note')
        self._off(features.COMMENTS)
        self.client.force_login(self.approver)
        page = self.client.get(change_request.get_absolute_url())
        self.assertContains(page, 'earlier note')
        self.assertNotContains(page, self._url('comment', change_request))
        self.client.post(self._url('comment', change_request), {'text': 'new'})
        self.assertEqual(ChangeRequestComment.objects.count(), 1)

    def test_comment_card_hidden_when_off_and_empty(self):
        change_request = self._request()
        self._off(features.COMMENTS)
        self.client.force_login(self.approver)
        self.assertNotContains(self.client.get(change_request.get_absolute_url()), 'id="comments"')

    def test_stand_ins_off(self):
        change_request = self._request()
        today = timezone.localdate()
        ApproverAbsence.objects.create(user=self.approver, stand_in=self.bob, starts=today)
        self.assertTrue(permissions.can_decide(self.bob, change_request))
        self._off(features.STAND_INS)
        self.assertFalse(permissions.can_decide(self.bob, change_request))

        self.client.force_login(self.approver)
        url = reverse('plugins:netbox_change_approval:away')
        page = self.client.get(url)
        self.assertContains(page, 'Stand-ins are turned off')
        self.assertNotContains(page, 'name="stand_in"')
        self.client.post(url, {'clear': '1'})
        self.assertTrue(ApproverAbsence.objects.exists())

    def test_notifications_off(self):
        self._off(features.NOTIFICATIONS)
        with self.captureOnCommitCallbacks(execute=True):
            change_request = self._request()
        self.assertFalse(Notification.objects.filter(
            object_type=ContentType.objects.get_for_model(ChangeRequest), object_id=change_request.pk,
        ).exists())

    def test_notifications_on(self):
        with self.captureOnCommitCallbacks(execute=True):
            change_request = self._request()
        self.assertTrue(Notification.objects.filter(
            user=self.approver, object_type=ContentType.objects.get_for_model(ChangeRequest),
            object_id=change_request.pk,
        ).exists())

    def test_widget_drawings_off(self):
        from ..widgets import REQUESTER_IDLE_LINES, ApprovalsWidget

        self._off(features.WIDGET_DRAWINGS)
        request = RequestFactory().get('/')
        request.user = self.submitter
        html = ApprovalsWidget().render(request)
        self.assertIn('nbca-idle', html)
        self.assertNotIn('<svg', html.split('nbca-idle">', 1)[1])
        self.assertTrue(any(line in html for line in REQUESTER_IDLE_LINES))
