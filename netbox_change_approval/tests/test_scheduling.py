from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from dcim.models import Manufacturer
from extras.models import Notification
from users.models import Group, User

from .. import expiry, gating, notifications, permissions, scheduling
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request


class ScheduledApplyTests(TestCase):
    """Approve now, apply at a chosen time."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers, _ = Group.objects.get_or_create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.submitter = User.objects.create(username='submitter')

    def _request(self):
        obj = Manufacturer.objects.create(name='V', slug='v')
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'changed'
        return gating.gate(obj, ActionChoices.UPDATE, fake_request(self.submitter))

    def _approve(self, change_request, apply_at):
        self.client.force_login(self.approver)
        return self.client.post(
            reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': change_request.pk}),
            {'confirm': True, 'apply_at': apply_at},
        )

    def test_scheduled_then_applied_when_due(self):
        change_request = self._request()
        when = timezone.localtime(timezone.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        with self.captureOnCommitCallbacks(execute=True):
            self._approve(change_request, when)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertIsNotNone(change_request.scheduled_for)
        self.assertEqual(change_request.scheduled_by, self.approver)
        self.assertEqual(Manufacturer.objects.get(pk=change_request.object_id).description, '')
        self.assertTrue(ChangeRequest.is_object_locked(change_request.content_type, change_request.object_id))
        pending = ChangeRequest.objects.filter(status=StatusChoices.PENDING)
        self.assertNotIn(change_request, permissions.approver_queue(self.approver, pending))
        note = Notification.objects.get(user=self.submitter, object_type=ContentType.objects.get_for_model(ChangeRequest))
        self.assertEqual(note.event_type, notifications.SCHEDULED)

        self.assertEqual(scheduling.apply_due(), 0)
        self.assertEqual(scheduling.apply_due(now=timezone.now() + timedelta(hours=3)), 1)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertEqual(change_request.decided_by, self.approver)
        self.assertEqual(Manufacturer.objects.get(pk=change_request.object_id).description, 'changed')

    def test_empty_time_applies_now(self):
        change_request = self._request()
        self._approve(change_request, '')
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)

    def test_conflict_when_due_cancels_schedule(self):
        change_request = self._request()
        when = timezone.now() + timedelta(hours=1)
        ChangeRequest.objects.filter(pk=change_request.pk).update(scheduled_for=when, scheduled_by=self.approver)
        Manufacturer.objects.filter(pk=change_request.object_id).update(description='someone else')
        self.assertEqual(scheduling.apply_due(now=when + timedelta(minutes=1)), 0)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertIsNone(change_request.scheduled_for)

    def test_apply_now_and_cancel_buttons(self):
        change_request = self._request()
        when = timezone.now() + timedelta(hours=1)
        ChangeRequest.objects.filter(pk=change_request.pk).update(scheduled_for=when, scheduled_by=self.approver)
        self.client.force_login(self.approver)
        url = reverse('plugins:netbox_change_approval:changerequest_schedule', kwargs={'pk': change_request.pk})
        self.assertContains(self.client.get(change_request.get_absolute_url()), 'Apply now')
        self.client.post(url, {'action': 'cancel'})
        change_request.refresh_from_db()
        self.assertIsNone(change_request.scheduled_for)
        ChangeRequest.objects.filter(pk=change_request.pk).update(scheduled_for=when, scheduled_by=self.approver)
        self.client.post(url, {'action': 'apply_now'})
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)

    def test_scheduled_requests_do_not_expire(self):
        change_request = self._request()
        ChangeRequest.objects.filter(pk=change_request.pk).update(
            expires_at=timezone.now() - timedelta(days=1), scheduled_for=timezone.now() + timedelta(days=1),
            scheduled_by=self.approver,
        )
        self.assertEqual(expiry.expire_overdue(), 0)

    def test_not_applied_once_decided_meanwhile(self):
        """A request retracted after the job read it is left alone."""
        change_request = self._request()
        ChangeRequest.objects.filter(pk=change_request.pk).update(
            scheduled_for=timezone.now(), scheduled_by=self.approver,
        )
        change_request.refresh_from_db()
        ChangeRequest.objects.filter(pk=change_request.pk).update(status=StatusChoices.RETRACTED)
        self.assertIsNone(scheduling.apply_scheduled(change_request))
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.RETRACTED)
        self.assertEqual(Manufacturer.objects.get(pk=change_request.object_id).description, '')
