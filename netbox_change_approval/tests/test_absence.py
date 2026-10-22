from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from dcim.models import Manufacturer
from extras.models import Notification
from users.models import Group, User

from .. import gating, permissions
from ..models import ActionChoices, ApproverAbsence, ChangeRequest, StatusChoices
from .utils import fake_request


class StandInTests(TestCase):
    """An away approver's stand-in decides their groups' requests."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers, _ = Group.objects.get_or_create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.alice.groups.add(cls.approvers)
        cls.bob = User.objects.create(username='bob')  # not an approver
        cls.submitter = User.objects.create(username='submitter')

    def _request(self, name='Vendor'):
        obj = Manufacturer.objects.create(name=name, slug=name.lower())
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'changed'
        return gating.gate(obj, ActionChoices.UPDATE, fake_request(self.submitter))

    def _away(self, starts_in=0, ends_in=None):
        today = timezone.localdate()
        return ApproverAbsence.objects.create(
            user=self.alice, stand_in=self.bob, starts=today + timedelta(days=starts_in),
            ends=today + timedelta(days=ends_in) if ends_in is not None else None,
        )

    def test_stand_in_can_decide_while_active(self):
        change_request = self._request()
        self.assertFalse(permissions.can_decide(self.bob, change_request))
        self._away()
        self.assertTrue(permissions.can_decide(self.bob, change_request))
        pending = ChangeRequest.objects.filter(status=StatusChoices.PENDING)
        self.assertIn(change_request, permissions.approver_queue(self.bob, pending))

        self.client.force_login(self.bob)
        self.client.post(
            reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': change_request.pk}),
            {'confirm': True},
        )
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)

    def test_planned_or_ended_absence_gives_nothing(self):
        change_request = self._request()
        absence = self._away(starts_in=2)
        self.assertFalse(permissions.can_decide(self.bob, change_request))
        absence.starts = timezone.localdate() - timedelta(days=5)
        absence.ends = timezone.localdate() - timedelta(days=1)
        absence.save()
        self.assertFalse(permissions.can_decide(self.bob, change_request))

    def test_stand_in_gets_notifications_instead(self):
        self._away()
        with self.captureOnCommitCallbacks(execute=True):
            change_request = self._request()
        notes = Notification.objects.filter(
            object_type=ContentType.objects.get_for_model(ChangeRequest), object_id=change_request.pk,
        )
        self.assertEqual(set(notes.values_list('user__username', flat=True)), {'bob'})

    def test_stand_in_cannot_decide_own_request(self):
        self._away()
        obj = Manufacturer.objects.create(name='Own', slug='own')
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'x'
        change_request = gating.gate(obj, ActionChoices.UPDATE, fake_request(self.bob))
        self.assertFalse(permissions.can_decide(self.bob, change_request))

    def test_away_page_sets_and_clears(self):
        self.client.force_login(self.alice)
        url = reverse('plugins:netbox_change_approval:away')
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'stand_in': self.bob.pk, 'starts': timezone.localdate().isoformat()})
        self.assertEqual(ApproverAbsence.objects.get(user=self.alice).stand_in, self.bob)
        self.client.post(url, {'stand_in': self.alice.pk, 'starts': timezone.localdate().isoformat()})
        self.assertEqual(ApproverAbsence.objects.get(user=self.alice).stand_in, self.bob)  # self refused
        self.client.post(url, {'clear': '1'})
        self.assertFalse(ApproverAbsence.objects.filter(user=self.alice).exists())


class PageRenderTests(TestCase):
    """The inbox button on every page asks which groups a user decides for,
    stand-ins included. That query must work on every page (it failed with
    "Cannot resolve keyword 'users'", shown on e.g. the API Tokens page)."""

    def test_pages_render_without_plugin_errors(self):
        approvers, _ = Group.objects.get_or_create(name='Change Approvers')
        alice = User.objects.create(username='alice2')
        alice.groups.add(approvers)
        bob = User.objects.create(username='bob2')
        ApproverAbsence.objects.create(user=alice, stand_in=bob, starts=timezone.localdate())

        from ..absence import covered_group_ids
        self.assertEqual(covered_group_ids(bob), {approvers.pk})

        for user in (alice, bob):
            self.client.force_login(user)
            for url in (reverse('home'), reverse('users:token_list')):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertNotContains(response, 'An error occurred when loading content from plugin')
