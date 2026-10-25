from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings

from dcim.models import Device, DeviceRole, DeviceType, Interface, InterfaceTemplate, Manufacturer, Site
from ipam.models import IPAddress
from netbox.context import current_request
from users.models import Group, User

from .. import gating
from ..exceptions import ObjectLocked
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request

PLUGINS_CONFIG_OFF = {
    'netbox_change_approval': {
        'default_approver_group': 'Change Approvers',
        'exempt_models': [],
        'group_components_with_parent': False,
    },
}


class ComponentsOfPendingDeviceTests(TestCase):
    """Interfaces and other components of a new device that is still pending
    belong to the device's request."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.alice = User.objects.create(username='alice')
        cls.bob = User.objects.create(username='bob')
        cls.site = Site.objects.create(name='S1', slug='s1')
        manufacturer = Manufacturer.objects.create(name='M', slug='m')
        cls.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Switch', slug='switch')
        for i in range(2):
            InterfaceTemplate.objects.create(device_type=cls.device_type, name=f'eth{i}', type='1000base-t')
        cls.role = DeviceRole.objects.create(name='R', slug='r')

    def setUp(self):
        self.device = Device(name='sw1', site=self.site, device_type=self.device_type, role=self.role)
        request = fake_request(self.alice)
        token = current_request.set(request)
        try:
            gating.gate(self.device, ActionChoices.CREATE, request)
            self.device.save()
        finally:
            current_request.reset(token)
        self.device_request = ChangeRequest.objects.get(object_id=self.device.pk, action=ActionChoices.CREATE)
        self.interface = Interface.objects.get(device=self.device, name='eth0')

    def _gate(self, instance, action, user):
        return gating.gate(instance, action, fake_request(user))

    def test_template_interfaces_have_no_requests_of_their_own(self):
        interface_ct = ContentType.objects.get_for_model(Interface)
        self.assertFalse(ChangeRequest.objects.filter(content_type=interface_ct).exists())

    def test_submitter_edit_folds_into_device_request(self):
        self.interface.description = 'uplink'
        self.assertIsNone(self._gate(self.interface, ActionChoices.UPDATE, self.alice))
        self.device_request.refresh_from_db()
        self.assertEqual(self.device_request.revision, 2)
        self.assertEqual(self.device_request.status, StatusChoices.PENDING)
        self.assertEqual(ChangeRequest.objects.count(), 1)

    def test_one_revision_per_page_submit(self):
        request = fake_request(self.alice)
        for interface in Interface.objects.filter(device=self.device):
            interface.description = 'bulk'
            gating.gate(interface, ActionChoices.UPDATE, request)
        self.device_request.refresh_from_db()
        self.assertEqual(self.device_request.revision, 2)

    def test_submitter_can_add_and_delete_components(self):
        new = Interface(device=self.device, name='eth9', type='1000base-t')
        self.assertIsNone(self._gate(new, ActionChoices.CREATE, self.alice))
        self.assertIsNone(self._gate(self.interface, ActionChoices.DELETE, self.alice))
        self.assertEqual(ChangeRequest.objects.count(), 1)

    def test_others_are_blocked(self):
        self.interface.description = 'bob'
        with self.assertRaises(ObjectLocked) as caught:
            self._gate(self.interface, ActionChoices.UPDATE, self.bob)
        self.assertEqual(caught.exception.change_request, self.device_request)
        with self.assertRaises(ObjectLocked):
            self._gate(Interface(device=self.device, name='eth8', type='1000base-t'), ActionChoices.CREATE, self.bob)

    def test_ip_on_pending_interface_belongs_to_device(self):
        ip = IPAddress(address='192.0.2.1/24', assigned_object=self.interface)
        with self.assertRaises(ObjectLocked):
            self._gate(ip, ActionChoices.CREATE, self.bob)
        self.assertIsNone(self._gate(ip, ActionChoices.CREATE, self.alice))

    def test_moving_approved_interface_onto_pending_device_needs_approval(self):
        # Otherwise the submitter could take an approved device's interface
        # without approval (and withdrawing the new device would delete it).
        other = Device.objects.create(name='sw2', site=self.site, device_type=self.device_type, role=self.role)
        approved = Interface.objects.get(device=other, name='eth0')
        approved.device = self.device
        change_request = self._gate(approved, ActionChoices.UPDATE, self.alice)
        self.assertIsNotNone(change_request)
        self.assertEqual(change_request.content_type_id, ContentType.objects.get_for_model(Interface).pk)
        self.device_request.refresh_from_db()
        self.assertEqual(self.device_request.revision, 1)

    def test_moving_pending_interface_onto_approved_device_needs_approval(self):
        other = Device.objects.create(name='sw2', site=self.site, device_type=self.device_type, role=self.role)
        self.interface.device = other
        self.interface.name = 'eth7'
        self.assertIsNotNone(self._gate(self.interface, ActionChoices.UPDATE, self.alice))

    def test_assigning_existing_ip_to_pending_interface_needs_approval(self):
        ip = IPAddress.objects.create(address='192.0.2.5/24')
        ip.assigned_object = self.interface
        change_request = self._gate(ip, ActionChoices.UPDATE, self.alice)
        self.assertIsNotNone(change_request)
        self.assertEqual(change_request.content_type_id, ContentType.objects.get_for_model(IPAddress).pk)

    def test_rejecting_device_removes_components(self):
        self.device_request.reject(self.approver, 'no')
        self.assertFalse(Interface.objects.filter(pk=self.interface.pk).exists())

    def test_after_approval_components_are_gated_normally(self):
        self.device_request.apply(user=self.approver)
        self.interface.description = 'after'
        change_request = self._gate(self.interface, ActionChoices.UPDATE, self.alice)
        self.assertIsNotNone(change_request)
        self.assertEqual(change_request.content_type_id, ContentType.objects.get_for_model(Interface).pk)

    @override_settings(PLUGINS_CONFIG=PLUGINS_CONFIG_OFF)
    def test_setting_off_restores_separate_requests(self):
        self.interface.description = 'own request'
        change_request = self._gate(self.interface, ActionChoices.UPDATE, self.bob)
        self.assertIsNotNone(change_request)
        self.assertEqual(change_request.content_type_id, ContentType.objects.get_for_model(Interface).pk)

    def test_lists_banner_and_request_page(self):
        admin = User.objects.create(username='admin', is_superuser=True)
        self.client.force_login(admin)
        html = self.client.get('/dcim/interfaces/').content.decode()
        self.assertIn('nbca-pending-create', html)
        self.assertIn(f'Part of new sw1 (request #{self.device_request.pk})', html)
        self.assertContains(self.client.get(self.interface.get_absolute_url()), 'Part of a new object waiting for approval')
        page = self.client.get(self.device_request.get_absolute_url())
        self.assertContains(page, 'Included with it')
        self.assertContains(page, f'device_id={self.device.pk}')


class CablesFollowPendingDeviceTests(ComponentsOfPendingDeviceTests):
    """A new cable with an end on a pending new device joins its request."""

    def setUp(self):
        super().setUp()
        self.existing = Device.objects.create(
            name='pp1', site=self.site, device_type=self.device_type, role=self.role,
        )
        self.panel_port = Interface.objects.get(device=self.existing, name='eth0')

    def _cable(self):
        from dcim.models import Cable

        return Cable(a_terminations=[self.interface], b_terminations=[self.panel_port])

    def test_submitter_cable_joins_device_request(self):
        self.assertIsNone(self._gate(self._cable(), ActionChoices.CREATE, self.alice))
        self.device_request.refresh_from_db()
        self.assertEqual(self.device_request.revision, 2)
        self.assertEqual(ChangeRequest.objects.count(), 1)

    def test_others_blocked_from_cabling_pending_device(self):
        with self.assertRaises(ObjectLocked):
            self._gate(self._cable(), ActionChoices.CREATE, self.bob)

    def test_existing_cable_moved_onto_pending_device_needs_approval(self):
        from dcim.models import Cable

        other = Device.objects.create(name='pp2', site=self.site, device_type=self.device_type, role=self.role)
        cable = Cable(a_terminations=[self.panel_port], b_terminations=[Interface.objects.get(device=other, name='eth0')])
        cable.save()
        cable = Cable.objects.get(pk=cable.pk)
        cable.b_terminations = [self.interface]
        change_request = self._gate(cable, ActionChoices.UPDATE, self.alice)
        self.assertIsNotNone(change_request)
        self.assertNotEqual(change_request.pk, self.device_request.pk)

    def test_request_page_counts_cables(self):
        self._cable().save()
        self.assertIn('Cables', [label for label, _count, _url in __import__(
            'netbox_change_approval.components', fromlist=['component_summary']
        ).component_summary(self.device_request)])


class ComponentChangeLogTests(TestCase):
    """Change log entries for components of a pending device say which
    request they belong to; bumping the request writes no entry of its own."""

    @classmethod
    def setUpTestData(cls):
        from core.models import ObjectType
        from users.models import ObjectPermission

        Group.objects.get_or_create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        perm = ObjectPermission.objects.create(name='dcim', actions=['view', 'add', 'change', 'delete'])
        perm.object_types.set([ObjectType.objects.get_for_model(m) for m in (Device, Interface)])
        perm.users.set([cls.alice])
        cls.site = Site.objects.create(name='S1', slug='s1')
        manufacturer = Manufacturer.objects.create(name='M', slug='m')
        cls.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Switch', slug='switch')
        InterfaceTemplate.objects.create(device_type=cls.device_type, name='eth0', type='1000base-t')
        cls.role = DeviceRole.objects.create(name='R', slug='r')

    def test_interface_edit_is_labeled(self):
        from core.models import ObjectChange

        device = Device(name='sw1', site=self.site, device_type=self.device_type, role=self.role)
        request = fake_request(self.alice)
        token = current_request.set(request)
        try:
            gating.gate(device, ActionChoices.CREATE, request)
            device.save()
        finally:
            current_request.reset(token)
        change_request = ChangeRequest.objects.get(object_id=device.pk, action=ActionChoices.CREATE)
        interface = Interface.objects.get(device=device)

        self.client.force_login(self.alice)
        self.client.post(f'/dcim/interfaces/{interface.pk}/edit/', {
            'device': device.pk, 'name': 'eth0', 'type': '1000base-t', 'description': 'uplink', '_update': '',
        })
        interface.refresh_from_db()
        self.assertEqual(interface.description, 'uplink')
        entry = ObjectChange.objects.filter(
            changed_object_type=ContentType.objects.get_for_model(Interface), changed_object_id=interface.pk,
        ).order_by('-time').first()
        self.assertIn(f'Part of change request #{change_request.pk}', entry.message)
        self.assertFalse(ObjectChange.objects.filter(
            changed_object_type=ContentType.objects.get_for_model(ChangeRequest),
            changed_object_id=change_request.pk, message__contains='Resubmitted',
        ).exists())
