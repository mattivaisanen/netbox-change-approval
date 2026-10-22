import uuid

from django.test import TestCase
from django.urls import reverse

from dcim.models import Manufacturer
from users.models import Group, User

from ..models import ActionChoices, ChangeRequest, StatusChoices


class MixedBatchTests(TestCase):
    """One bulk edit can produce requests for different approver groups.
    Deciding the batch only decides the requests routed to you."""

    @classmethod
    def setUpTestData(cls):
        cls.group_a = Group.objects.create(name='Team A')
        cls.group_b = Group.objects.create(name='Team B')
        cls.alice = User.objects.create(username='alice')
        cls.approver_a = User.objects.create(username='approver-a')
        cls.approver_a.groups.add(cls.group_a)
        cls.approver_b = User.objects.create(username='approver-b')
        cls.approver_b.groups.add(cls.group_b)

    def setUp(self):
        batch = uuid.uuid4()
        self.requests = []
        for name, group in (('a', self.group_a), ('b', self.group_b)):
            obj = Manufacturer.objects.create(name=f'Vendor {name}', slug=f'vendor-{name}')
            self.requests.append(ChangeRequest.objects.create(
                content_type_id=ChangeRequest._meta.get_field('content_type').related_model.objects.get_for_model(
                    Manufacturer).pk,
                object_id=obj.pk, object_repr=str(obj), action=ActionChoices.UPDATE,
                diff={'description': {'old': '', 'new': f'new {name}'}},
                submitted_by=self.alice, approver_group=group, batch_id=batch,
            ))

    def _post(self, user, name, data):
        self.client.force_login(user)
        url = reverse(f'plugins:netbox_change_approval:changerequest_{name}', kwargs={'pk': self.requests[0].pk})
        return self.client.post(url, data)

    def _statuses(self):
        return [ChangeRequest.objects.get(pk=cr.pk).status for cr in self.requests]

    def test_approving_batch_leaves_other_groups_request_pending(self):
        self._post(self.approver_a, 'approve', {'confirm': True})

        self.assertEqual(self._statuses()[0], StatusChoices.APPLIED)
        self.assertEqual(self._statuses()[1], StatusChoices.PENDING)
        self.assertEqual(Manufacturer.objects.get(slug='vendor-b').description, '')

    def test_rejecting_batch_leaves_other_groups_request_pending(self):
        self._post(self.approver_a, 'reject', {'comment': 'no', 'confirm': True})

        self.assertEqual(self._statuses(), [StatusChoices.REJECTED, StatusChoices.PENDING])

    def test_bulk_approve_skips_other_groups_batch_members(self):
        self.client.force_login(self.approver_a)
        self.client.post(reverse('plugins:netbox_change_approval:changerequest_bulk_approve'), {
            'pk': [self.requests[0].pk], '_confirm': '1',
        })

        self.assertEqual(self._statuses(), [StatusChoices.APPLIED, StatusChoices.PENDING])

    def test_submitter_retracts_whole_batch(self):
        self._post(self.alice, 'retract', {'confirm': True})

        self.assertEqual(self._statuses(), [StatusChoices.RETRACTED, StatusChoices.RETRACTED])


class MixedBatchNotificationTests(TestCase):
    def test_each_approver_group_is_notified(self):
        from django.contrib.contenttypes.models import ContentType
        from extras.models import Notification

        group_a = Group.objects.create(name='Team A')
        group_b = Group.objects.create(name='Team B')
        submitter = User.objects.create(username='alice')
        approver_a = User.objects.create(username='approver-a')
        approver_a.groups.add(group_a)
        approver_b = User.objects.create(username='approver-b')
        approver_b.groups.add(group_b)
        ct = ContentType.objects.get_for_model(Manufacturer)
        batch = uuid.uuid4()

        with self.captureOnCommitCallbacks(execute=True):
            for name, group in (('a', group_a), ('b', group_b)):
                obj = Manufacturer.objects.create(name=f'Vendor {name}', slug=f'vendor-{name}')
                ChangeRequest.objects.create(
                    content_type=ct, object_id=obj.pk, object_repr=str(obj), action=ActionChoices.UPDATE,
                    diff={'description': {'old': '', 'new': 'x'}}, submitted_by=submitter,
                    approver_group=group, batch_id=batch,
                )

        self.assertTrue(Notification.objects.filter(user=approver_a).exists())
        self.assertTrue(Notification.objects.filter(user=approver_b).exists())
