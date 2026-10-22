"""
Two people deciding the same request at about the same time: whoever comes
second gets "already decided" and changes nothing, and an applied change is
never turned into a rejected one (or a new object deleted after approval).
"""
from django.test import TestCase

from dcim.models import Manufacturer
from netbox.context import current_request
from users.models import Group, User

from .. import approvals, gating
from ..exceptions import AlreadyDecided
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request


class ConcurrentDecisionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.anna = User.objects.create(username='anna')
        cls.ben = User.objects.create(username='ben')
        for user in (cls.anna, cls.ben):
            user.groups.add(cls.approvers)
        cls.alice = User.objects.create(username='alice')

    def _as_alice(self, fn):
        request = fake_request(self.alice)
        token = current_request.set(request)
        try:
            return fn(request)
        finally:
            current_request.reset(token)

    def _pending_create(self):
        vendor = Manufacturer(name='Newco', slug='newco')
        self._as_alice(lambda request: (gating.gate(vendor, ActionChoices.CREATE, request), vendor.save()))
        return vendor, ChangeRequest.objects.get(object_id=vendor.pk, action=ActionChoices.CREATE)

    def _pending_update(self):
        vendor = Manufacturer.objects.create(name='Acme', slug='acme')
        vendor.description = 'new'
        return vendor, self._as_alice(lambda request: gating.gate(vendor, ActionChoices.UPDATE, request))

    def test_reject_after_someone_approved_changes_nothing(self):
        vendor, change_request = self._pending_create()
        stale = ChangeRequest.objects.get(pk=change_request.pk)  # Ben's page, loaded before Anna approved

        approvals.approve([change_request], self.anna)
        with self.assertRaises(AlreadyDecided):
            stale.reject(self.ben, 'too late')

        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertTrue(Manufacturer.objects.filter(pk=vendor.pk).exists())

    def test_send_back_and_retract_after_rejection_change_nothing(self):
        _vendor, change_request = self._pending_update()
        stale_a = ChangeRequest.objects.get(pk=change_request.pk)
        stale_b = ChangeRequest.objects.get(pk=change_request.pk)

        change_request.reject(self.anna, 'no')
        with self.assertRaises(AlreadyDecided):
            stale_a.send_back(self.ben, 'fix it')
        with self.assertRaises(AlreadyDecided):
            stale_b.retract(self.alice)
        with self.assertRaises(approvals.AlreadyDecided):
            approvals.approve([stale_a], self.ben)

        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.REJECTED)
        self.assertEqual(change_request.decided_by, self.anna)

    def test_reject_view_says_already_decided(self):
        _vendor, change_request = self._pending_update()
        self.client.force_login(self.ben)
        page = self.client.get(change_request.get_absolute_url() + 'reject/')
        self.assertEqual(page.status_code, 200)
        # Anna approves while Ben's reject form is open; Ben then submits.
        approvals.approve([ChangeRequest.objects.get(pk=change_request.pk)], self.anna)
        response = self.client.post(change_request.get_absolute_url() + 'reject/', {'comment': 'no'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Request #{change_request.pk} is already applied; nothing changed.')
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)

    def test_batch_reject_is_all_or_nothing(self):
        _vendor, first = self._pending_update()
        second_vendor = Manufacturer.objects.create(name='Other', slug='other')
        second_vendor.description = 'x'
        second = self._as_alice(lambda request: gating.gate(second_vendor, ActionChoices.UPDATE, request))
        stale_second = ChangeRequest.objects.get(pk=second.pk)
        approvals.approve([second], self.anna)

        from django.db import transaction
        with self.assertRaises(AlreadyDecided):
            with transaction.atomic():
                ChangeRequest.objects.get(pk=first.pk).reject(self.ben, 'no')
                stale_second.reject(self.ben, 'no')

        first.refresh_from_db()
        self.assertEqual(first.status, StatusChoices.PENDING)
