from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from ipam.models import IPAddress
from users.models import Group, ObjectPermission, User

from ..models import ChangeRequest, StatusChoices


class ImportAssignmentTests(TestCase):
    """The IP address import form sets the assigned interface in save(),
    after validation. Moving an IP to another interface by import needs
    approval like any other change."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.create(name='Change Approvers')
        cls.user = User.objects.create(username='importer')
        perm = ObjectPermission.objects.create(name='import-test', enabled=True, actions=['view', 'add', 'change'])
        perm.object_types.set([
            ObjectType.objects.get_for_model(m) for m in (IPAddress, Device, Interface)
        ])
        perm.users.set([cls.user])
        site = Site.objects.create(name='S1', slug='s1')
        manufacturer = Manufacturer.objects.create(name='M', slug='m')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='T', slug='t')
        role = DeviceRole.objects.create(name='R', slug='r')
        cls.device = Device.objects.create(name='d1', site=site, device_type=device_type, role=role)
        cls.eth0 = Interface.objects.create(device=cls.device, name='eth0', type='1000base-t')
        cls.eth1 = Interface.objects.create(device=cls.device, name='eth1', type='1000base-t')
        cls.ip = IPAddress.objects.create(address='192.0.2.1/24', assigned_object=cls.eth0)

    def test_import_moving_ip_to_other_interface_is_gated(self):
        self.client.force_login(self.user)
        csv = f'id,device,interface\n{self.ip.pk},d1,eth1'
        self.client.post(reverse('ipam:ipaddress_bulk_import'), {
            'data': csv, 'format': 'csv', 'csv_delimiter': ',', 'import_method': 'direct',
        })

        self.ip.refresh_from_db()
        self.assertEqual(self.ip.assigned_object_id, self.eth0.pk)
        self.assertTrue(ChangeRequest.objects.filter(object_id=self.ip.pk, status=StatusChoices.PENDING).exists())


class SecondObjectCreatedByFormTests(TestCase):
    """The FHRP group form can create a virtual IP address in its save(),
    without validating it. That IP is a pending create like any other."""

    def test_fhrp_virtual_ip_is_pending(self):
        from ipam.models import FHRPGroup

        Group.objects.create(name='Change Approvers')
        user = User.objects.create(username='fhrp-editor')
        perm = ObjectPermission.objects.create(name='fhrp-test', enabled=True, actions=['view', 'add', 'change'])
        perm.object_types.set([ObjectType.objects.get_for_model(m) for m in (FHRPGroup, IPAddress)])
        perm.users.set([user])
        group = FHRPGroup.objects.create(protocol='vrrp2', group_id=1)

        self.client.force_login(user)
        self.client.post(reverse('ipam:fhrpgroup_edit', kwargs={'pk': group.pk}), {
            'protocol': 'vrrp2', 'group_id': 1, 'ip_address': '198.51.100.1/24', 'ip_status': 'active',
        })

        ip = IPAddress.objects.get(address='198.51.100.1/24')
        self.assertTrue(ChangeRequest.objects.filter(
            object_id=ip.pk, action='create', status=StatusChoices.PENDING,
        ).exists())
