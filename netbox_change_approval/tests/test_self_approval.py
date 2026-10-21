from django.test import TestCase
from django.urls import reverse

from dcim.models import Manufacturer
from users.models import Group, User

from .. import gating, inbox, permissions
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request


class SelfApprovalTests(TestCase):
    """Nobody may approve, reject or send back their own request, not even an
    approver or a superuser."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.colleague = User.objects.create(username='colleague')
        cls.colleague.groups.add(cls.approvers)
        cls.admin = User.objects.create(username='admin', is_superuser=True)

    def _request_by(self, user):
        obj = Manufacturer.objects.create(name=f'Vendor {user.username}', slug=f'vendor-{user.username}')
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'changed'
        change_request = gating.gate(obj, ActionChoices.UPDATE, fake_request(user))
        self.assertIsNotNone(change_request)
        return change_request

    def test_approver_cannot_approve_own_request(self):
        change_request = self._request_by(self.approver)
        self.client.force_login(self.approver)

        url = reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': change_request.pk})
        self.client.post(url, {'confirm': True})

        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(Manufacturer.objects.get(pk=change_request.object_id).description, '')

    def test_superuser_cannot_approve_own_request(self):
        change_request = ChangeRequest.objects.create(
            content_type=self._request_by(self.approver).content_type,
            object_id=None, object_repr='x', action=ActionChoices.DELETE,
            submitted_by=self.admin, approver_group=self.approvers,
        )
        self.assertFalse(permissions.can_decide(self.admin, change_request))

    def test_own_request_cannot_be_rejected_or_sent_back(self):
        change_request = self._request_by(self.approver)
        self.client.force_login(self.approver)
        for name in ('changerequest_reject', 'changerequest_sendback'):
            url = reverse(f'plugins:netbox_change_approval:{name}', kwargs={'pk': change_request.pk})
            self.client.post(url, {'comment': 'no', 'confirm': True})
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)

    def test_other_approver_can_approve(self):
        change_request = self._request_by(self.approver)
        self.assertTrue(permissions.can_decide(self.colleague, change_request))
        self.client.force_login(self.colleague)
        url = reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': change_request.pk})
        self.client.post(url, {'confirm': True})
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)

    def test_own_request_not_in_queue_inbox_or_bulk_approve(self):
        change_request = self._request_by(self.approver)
        pending = ChangeRequest.objects.filter(status=StatusChoices.PENDING)

        self.assertNotIn(change_request, permissions.approver_queue(self.approver, pending))
        self.assertIn(change_request, permissions.approver_queue(self.colleague, pending))
        self.assertEqual(inbox.inbox_count(self.approver), 0)

        self.client.force_login(self.approver)
        url = reverse('plugins:netbox_change_approval:changerequest_bulk_approve')
        self.client.post(url, {'pk': [change_request.pk], '_confirm': '1'})
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)

    def test_detail_page_hides_decision_buttons_on_own_request(self):
        change_request = self._request_by(self.approver)
        self.client.force_login(self.approver)
        response = self.client.get(change_request.get_absolute_url())
        self.assertNotContains(
            response,
            reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': change_request.pk}),
        )
