from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Manufacturer, Site
from netbox.context import current_request
from users.models import Group, ObjectPermission, User

from ..context import applying_change_on
from ..models import ActionChoices, ChangeRequest, StatusChoices
from ..validators import ApprovalGateValidator
from .utils import fake_request, grant_bypass


class PlainSaveBackstopTests(TestCase):
    """Updates written with a plain save() (no full_clean) still need approval."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.create(name='Change Approvers')
        cls.user = User.objects.create(username='saver')

    def setUp(self):
        self.token = current_request.set(fake_request(self.user))

    def tearDown(self):
        current_request.reset(self.token)

    def test_plain_save_is_queued_and_not_written(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'sneaky'
        obj.save()

        obj.refresh_from_db()
        self.assertEqual(obj.description, '')
        cr = ChangeRequest.objects.get(object_id=obj.pk, action=ActionChoices.UPDATE)
        self.assertEqual(cr.status, StatusChoices.PENDING)
        self.assertEqual(cr.diff['description']['new'], 'sneaky')

    def test_validated_save_is_not_gated_twice(self):
        grant_bypass(self.user, Manufacturer)
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'allowed'
        ApprovalGateValidator().validate(obj, current_request.get())
        obj.save()

        obj.refresh_from_db()
        self.assertEqual(obj.description, 'allowed')
        self.assertFalse(ChangeRequest.objects.exists())

    def test_bypass_user_plain_save_goes_through(self):
        grant_bypass(self.user, Manufacturer)
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'allowed'
        obj.save()

        obj.refresh_from_db()
        self.assertEqual(obj.description, 'allowed')

    def test_applying_change_is_not_gated(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'approved'
        with applying_change_on():
            obj.save()

        obj.refresh_from_db()
        self.assertEqual(obj.description, 'approved')

    def test_internal_field_only_save_is_not_gated(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.save()  # nothing editable changed
        self.assertFalse(ChangeRequest.objects.exists())

    def test_second_change_to_locked_object_is_dropped(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'first'
        obj.save()
        obj.description = 'second'
        obj.save()

        obj.refresh_from_db()
        self.assertEqual(obj.description, '')
        self.assertEqual(ChangeRequest.objects.count(), 1)


class BulkRenameTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        Group.objects.create(name='Change Approvers')
        cls.user = User.objects.create(username='renamer')
        perm = ObjectPermission.objects.create(
            name='rename-test', enabled=True, actions=['view', 'change'],
        )
        perm.object_types.set([ObjectType.objects.get_for_model(Site)])
        perm.users.set([cls.user])
        cls.sites = [Site.objects.create(name=f'Site {i}', slug=f's{i}') for i in range(3)]

    def test_bulk_rename_queues_every_object(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('dcim:site_bulk_rename'), {
            'pk': [s.pk for s in self.sites],
            'find': 'Site', 'replace': 'Place', '_apply': '1',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Site.objects.filter(name__startswith='Place').count(), 0)
        requests = ChangeRequest.objects.filter(action=ActionChoices.UPDATE, status=StatusChoices.PENDING)
        self.assertEqual(requests.count(), 3)
        self.assertEqual(len({cr.batch_id for cr in requests}), 1)


class ClusterAddDevicesTests(TestCase):
    """One of the NetBox screens that saves with a plain save()."""

    @classmethod
    def setUpTestData(cls):
        from dcim.models import Device, DeviceRole, DeviceType
        from virtualization.models import Cluster, ClusterType

        Group.objects.create(name='Change Approvers')
        cls.user = User.objects.create(username='clusterer')
        perm = ObjectPermission.objects.create(
            name='cluster-test', enabled=True, actions=['view', 'change'],
        )
        perm.object_types.set([
            ObjectType.objects.get_for_model(Cluster), ObjectType.objects.get_for_model(Device),
        ])
        perm.users.set([cls.user])

        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Maker', slug='maker')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Box', slug='box')
        role = DeviceRole.objects.create(name='Role', slug='role')
        cls.devices = [
            Device.objects.create(name=f'd{i}', site=site, device_type=device_type, role=role)
            for i in range(2)
        ]
        cls.cluster = Cluster.objects.create(
            name='C1', type=ClusterType.objects.create(name='T', slug='t'), scope=site,
        )

    def test_add_devices_is_queued(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('virtualization:cluster_add_devices', kwargs={'pk': self.cluster.pk}),
            {'devices': [d.pk for d in self.devices]},
        )

        for device in self.devices:
            device.refresh_from_db()
            self.assertIsNone(device.cluster_id)
        self.assertEqual(
            ChangeRequest.objects.filter(action=ActionChoices.UPDATE, status=StatusChoices.PENDING).count(), 2,
        )
