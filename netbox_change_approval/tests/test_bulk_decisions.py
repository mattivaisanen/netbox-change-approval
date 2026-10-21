from django.test import TestCase
from django.urls import reverse

from dcim.models import Manufacturer
from users.models import Group, User

from .. import gating
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request


class BulkDecisionViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.other_group = Group.objects.create(name='Other Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)

    def setUp(self):
        self.requests = []
        for n in range(3):
            obj = Manufacturer.objects.create(name=f'Vendor {n}', slug=f'vendor-{n}')
            obj = Manufacturer.objects.get(pk=obj.pk)
            obj.description = f'new {n}'
            self.requests.append(gating.gate(obj, ActionChoices.UPDATE, fake_request(self.alice)))

    def test_approvals_list_shows_bulk_buttons(self):
        self.client.force_login(self.approver)
        response = self.client.get(reverse('plugins:netbox_change_approval:changerequest_approvals'))
        self.assertContains(response, 'Approve Selected')
        self.assertContains(response, 'Reject Selected')

    def test_bulk_approve_applies_selected_only(self):
        self.client.force_login(self.approver)
        url = reverse('plugins:netbox_change_approval:changerequest_bulk_approve')
        pks = [self.requests[0].pk, self.requests[1].pk]

        confirm = self.client.post(url, {'pk': pks})
        self.assertContains(confirm, 'Approve and apply 2 requests?')

        self.client.post(url, {'pk': pks, '_confirm': '1'})

        statuses = {cr.pk: cr.status for cr in ChangeRequest.objects.all()}
        self.assertEqual(statuses[pks[0]], StatusChoices.APPLIED)
        self.assertEqual(statuses[pks[1]], StatusChoices.APPLIED)
        self.assertEqual(statuses[self.requests[2].pk], StatusChoices.PENDING)
        self.assertEqual(Manufacturer.objects.get(slug='vendor-0').description, 'new 0')

    def test_bulk_reject_needs_comment(self):
        self.client.force_login(self.approver)
        url = reverse('plugins:netbox_change_approval:changerequest_bulk_reject')
        pks = [cr.pk for cr in self.requests]

        response = self.client.post(url, {'pk': pks, '_confirm': '1', 'comment': ''})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ChangeRequest.objects.filter(status=StatusChoices.PENDING).count(), 3)

        self.client.post(url, {'pk': pks, '_confirm': '1', 'comment': 'Not this week'})
        self.assertEqual(ChangeRequest.objects.filter(status=StatusChoices.REJECTED).count(), 3)

    def test_requests_routed_elsewhere_are_skipped(self):
        ChangeRequest.objects.filter(pk=self.requests[0].pk).update(approver_group=self.other_group)
        self.client.force_login(self.approver)
        url = reverse('plugins:netbox_change_approval:changerequest_bulk_approve')

        self.client.post(url, {'pk': [self.requests[0].pk, self.requests[1].pk], '_confirm': '1'})

        self.assertEqual(ChangeRequest.objects.get(pk=self.requests[0].pk).status, StatusChoices.PENDING)
        self.assertEqual(ChangeRequest.objects.get(pk=self.requests[1].pk).status, StatusChoices.APPLIED)

    def test_conflict_keeps_one_pending_and_applies_rest(self):
        # Someone changed vendor-0 after the request was submitted.
        Manufacturer.objects.filter(slug='vendor-0').update(description='changed meanwhile')
        self.client.force_login(self.approver)
        url = reverse('plugins:netbox_change_approval:changerequest_bulk_approve')

        self.client.post(url, {'pk': [self.requests[0].pk, self.requests[1].pk], '_confirm': '1'})

        self.assertEqual(ChangeRequest.objects.get(pk=self.requests[0].pk).status, StatusChoices.PENDING)
        self.assertEqual(ChangeRequest.objects.get(pk=self.requests[1].pk).status, StatusChoices.APPLIED)
