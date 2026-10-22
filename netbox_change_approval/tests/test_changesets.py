from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Manufacturer
from extras.models import Notification
from users.models import Group, ObjectPermission, User

from .. import inbox, permissions
from ..models import ChangeRequest, ChangeSet, StatusChoices

PLUGINS_CONFIG_OFF = {
    'netbox_change_approval': {
        'default_approver_group': 'Change Approvers',
        'exempt_models': [],
        'change_sets': False,
    },
}


class ChangeSetTests(TestCase):
    """Changes made while a change set is active are collected, hidden from
    approvers until submitted, and decided as one."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers, _ = Group.objects.get_or_create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.alice = User.objects.create(username='alice')
        perm = ObjectPermission.objects.create(name='manufacturers', actions=['view', 'add', 'change', 'delete'])
        perm.object_types.set([ObjectType.objects.get_for_model(Manufacturer)])
        perm.users.set([cls.alice])

    def setUp(self):
        self.vendors = [Manufacturer.objects.create(name=f'V{i}', slug=f'v{i}') for i in range(2)]
        self.client.force_login(self.alice)

    def _start(self):
        self.client.post(reverse('plugins:netbox_change_approval:changeset_list'), {'name': 'Install sw1', 'ticket': 'CHG-1'})
        return ChangeSet.objects.get(name='Install sw1')

    def _edit_all(self):
        for vendor in self.vendors:
            self.client.post(reverse('dcim:manufacturer_edit', kwargs={'pk': vendor.pk}), {
                'name': vendor.name, 'slug': vendor.slug, 'description': 'changed', '_update': '',
            })

    def _action(self, change_set, action):
        return self.client.post(change_set.get_absolute_url(), {'action': action})

    def test_changes_collect_hidden_until_submitted(self):
        change_set = self._start()
        with self.captureOnCommitCallbacks(execute=True):
            self._edit_all()
        requests = list(change_set.requests())
        self.assertEqual(len(requests), 2)
        pending = ChangeRequest.objects.filter(status=StatusChoices.PENDING)
        self.assertFalse(permissions.approver_queue(self.approver, pending).exists())
        self.assertFalse(permissions.can_decide(self.approver, requests[0]))
        self.assertEqual(inbox.inbox_count(self.approver), 0)
        ct = ContentType.objects.get_for_model(ChangeRequest)
        self.assertFalse(Notification.objects.filter(object_type=ct, user=self.approver).exists())

        with self.captureOnCommitCallbacks(execute=True):
            self._action(change_set, 'submit')
        change_set.refresh_from_db()
        self.assertEqual(change_set.status, ChangeSet.SUBMITTED)
        self.assertEqual(inbox.inbox_count(self.approver), 1)  # one item
        self.assertEqual(Notification.objects.filter(object_type=ct, user=self.approver).count(), 1)

        self.client.force_login(self.approver)
        self.client.post(
            reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': requests[0].pk}),
            {'confirm': True},
        )
        for vendor in self.vendors:
            vendor.refresh_from_db()
            self.assertEqual(vendor.description, 'changed')

    def test_leave_stops_collecting(self):
        change_set = self._start()
        self._action(change_set, 'leave')
        self._edit_all()
        self.assertFalse(change_set.requests().exists())
        self.assertEqual(ChangeRequest.objects.filter(batch_id__isnull=True, status=StatusChoices.PENDING).count(), 2)

    def test_discard_withdraws_everything(self):
        change_set = self._start()
        self._edit_all()
        self._action(change_set, 'discard')
        self.assertFalse(ChangeSet.objects.filter(pk=change_set.pk).exists())
        self.assertFalse(ChangeRequest.objects.filter(status=StatusChoices.PENDING).exists())

    def test_banner_and_pages(self):
        change_set = self._start()
        self._edit_all()
        page = self.client.get(reverse('dcim:manufacturer_list'))
        self.assertContains(page, 'Working in change set')
        detail = self.client.get(change_set.get_absolute_url())
        self.assertContains(detail, 'Submit for approval')
        self.assertContains(detail, 'V1')

    @override_settings(PLUGINS_CONFIG=PLUGINS_CONFIG_OFF)
    def test_setting_off(self):
        ChangeSet.objects.create(name='x', created_by=self.alice)
        session = self.client.session
        session['nbca_change_set'] = ChangeSet.objects.get(name='x').pk
        session.save()
        self._edit_all()
        self.assertEqual(ChangeRequest.objects.filter(batch_id__isnull=True).count(), 2)
