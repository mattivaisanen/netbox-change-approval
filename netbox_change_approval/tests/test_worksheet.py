from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from dcim.models import Cable, Device, DeviceRole, DeviceType, Interface, InterfaceTemplate, Manufacturer, Rack, Site
from netbox.context import current_request
from users.models import Group, User

from .. import gating, worksheet
from ..models import ActionChoices, ChangeRequest, StatusChoices, WorkTask
from .utils import fake_request

PLUGINS_CONFIG_OFF = {
    'netbox_change_approval': {
        'default_approver_group': 'Change Approvers',
        'exempt_models': [],
        'work_sheets': False,
    },
}


class WorkSheetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers, _ = Group.objects.get_or_create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.alice = User.objects.create(username='alice')
        cls.site = Site.objects.create(name='S1', slug='s1')
        cls.rack = Rack.objects.create(name='A3', site=cls.site)
        cls.rack2 = Rack.objects.create(name='A4', site=cls.site)
        manufacturer = Manufacturer.objects.create(name='M', slug='m')
        cls.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Switch 48', slug='sw48', u_height=2)
        InterfaceTemplate.objects.create(device_type=cls.device_type, name='eth1', type='1000base-t')
        cls.role = DeviceRole.objects.create(name='R', slug='r')

    def _create_device(self, name='sw1', position=20):
        device = Device(name=name, site=self.site, rack=self.rack, position=position, face='front',
                        device_type=self.device_type, role=self.role)
        request = fake_request(self.alice)
        token = current_request.set(request)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                gating.gate(device, ActionChoices.CREATE, request)
                device.save()
        finally:
            current_request.reset(token)
        return device, ChangeRequest.objects.get(object_id=device.pk, action=ActionChoices.CREATE)

    def test_mount_task_shown_only_after_approval(self):
        device, change_request = self._create_device()
        task = WorkTask.objects.get(change_request=change_request)
        self.assertEqual(task.text, 'Mount sw1 (Switch 48) in rack A3, U20–21, front')
        self.assertEqual(task.rack, self.rack)
        self.assertFalse(worksheet.open_tasks().exists())
        change_request.apply(user=self.approver)
        self.assertEqual(list(worksheet.open_tasks()), [task])

    def test_cables_on_new_device_become_patch_tasks(self):
        device, change_request = self._create_device()
        panel = Device.objects.create(name='pp1', site=self.site, device_type=self.device_type, role=self.role)
        with self.captureOnCommitCallbacks(execute=True):
            cable = Cable(a_terminations=[Interface.objects.get(device=device)],
                          b_terminations=[Interface.objects.get(device=panel)])
            gating.gate(cable, ActionChoices.CREATE, fake_request(self.alice))
            cable.save()
        texts = list(WorkTask.objects.filter(change_request=change_request).values_list('text', flat=True))
        self.assertIn('Patch sw1 eth1 → pp1 eth1', texts)

    def test_move_task(self):
        device = Device.objects.create(name='sw2', site=self.site, rack=self.rack, position=10, face='front',
                                       device_type=self.device_type, role=self.role)
        device = Device.objects.get(pk=device.pk)
        device.rack, device.position = self.rack2, 30
        with self.captureOnCommitCallbacks(execute=True):
            change_request = gating.gate(device, ActionChoices.UPDATE, fake_request(self.alice))
        task = WorkTask.objects.get(change_request=change_request)
        self.assertEqual(task.text, 'Move sw2 from rack A3, U10–11, front to rack A4, U30–31, front')

    def test_rename_has_no_task(self):
        device = Device.objects.create(name='sw3', site=self.site, device_type=self.device_type, role=self.role)
        device = Device.objects.get(pk=device.pk)
        device.name = 'sw3-new'
        with self.captureOnCommitCallbacks(execute=True):
            change_request = gating.gate(device, ActionChoices.UPDATE, fake_request(self.alice))
        self.assertFalse(WorkTask.objects.filter(change_request=change_request).exists())

    def test_page_tick_and_undo(self):
        device, change_request = self._create_device()
        change_request.apply(user=self.approver)
        task = WorkTask.objects.get(change_request=change_request)
        self.client.force_login(self.alice)
        url = reverse('plugins:netbox_change_approval:worksheet')
        self.assertContains(self.client.get(url), 'Mount sw1')
        self.client.post(url, {'task': task.pk})
        task.refresh_from_db()
        self.assertEqual(task.done_by, self.alice)
        self.client.post(url, {'task': task.pk, 'undo': '1'})
        task.refresh_from_db()
        self.assertIsNone(task.done_at)
        admin = User.objects.create(username='admin', is_superuser=True)
        self.client.force_login(admin)
        self.assertContains(self.client.get(self.rack.get_absolute_url()), '1 open task')

    @override_settings(PLUGINS_CONFIG=PLUGINS_CONFIG_OFF)
    def test_setting_off_writes_no_tasks(self):
        self._create_device()
        self.assertFalse(WorkTask.objects.exists())


class InboxFieldWorkTests(WorkSheetTests):
    def test_open_tasks_in_inbox_and_tick_from_there(self):
        device, change_request = self._create_device(name='sw9')
        change_request.apply(user=self.approver)
        task = WorkTask.objects.get(change_request=change_request)
        self.client.force_login(self.alice)
        inbox_url = reverse('plugins:netbox_change_approval:changerequest_inbox')
        self.assertContains(self.client.get(inbox_url), 'Mount sw9')
        response = self.client.post(reverse('plugins:netbox_change_approval:worksheet'), {'task': task.pk, 'back': 'inbox'})
        self.assertTrue(response.url.startswith(inbox_url))
        task.refresh_from_db()
        self.assertIsNotNone(task.done_at)
        self.assertNotContains(self.client.get(inbox_url), 'Mount sw9')


class ComponentTasksTests(WorkSheetTests):
    def test_cable_added_while_pending_gets_task_after_approval(self):
        """A cable patched to the new device from the edit form (validated
        before NetBox's transaction, so the request was refreshed before the
        cable existed) still gets its own task once the device is approved."""
        from django.db import transaction

        device, change_request = self._create_device(name='sw7')
        panel = Device.objects.create(name='pp7', site=self.site, device_type=self.device_type, role=self.role)
        cable = Cable(a_terminations=[Interface.objects.get(device=device)],
                      b_terminations=[Interface.objects.get(device=panel)])
        with self.captureOnCommitCallbacks(execute=True):
            gating.gate(cable, ActionChoices.CREATE, fake_request(self.alice))
        WorkTask.objects.filter(change_request=change_request).delete()  # as if refreshed too early
        cable.save()
        with self.captureOnCommitCallbacks(execute=True):
            change_request.apply(user=self.approver)
        texts = sorted(worksheet.open_tasks().filter(change_request=change_request).values_list('text', flat=True))
        self.assertEqual(texts, ['Mount sw7 (Switch 48) in rack A3, U20–21, front', 'Patch sw7 eth1 → pp7 eth1'])


class MoreTaskKindsTests(WorkSheetTests):
    def test_ip_on_new_device_and_standalone_ips(self):
        from ipam.models import IPAddress

        device, change_request = self._create_device(name='sw8')
        eth = Interface.objects.get(device=device)
        IPAddress.objects.create(address='192.0.2.8/24', assigned_object=eth)
        with self.captureOnCommitCallbacks(execute=True):
            change_request.apply(user=self.approver)
        texts = list(worksheet.open_tasks().filter(change_request=change_request).values_list('text', flat=True))
        self.assertIn('Configure 192.0.2.8/24 on sw8 eth1', texts)

        with self.captureOnCommitCallbacks(execute=True):
            assigned = IPAddress(address='192.0.2.9/24', assigned_object=eth)
            request = fake_request(self.alice)
            token = current_request.set(request)
            try:
                gating.gate(assigned, ActionChoices.CREATE, request)
                assigned.save()
            finally:
                current_request.reset(token)
        ip_request = ChangeRequest.objects.get(object_id=assigned.pk, action=ActionChoices.CREATE,
                                               content_type=ContentType.objects.get_for_model(IPAddress))
        self.assertEqual(list(WorkTask.objects.filter(change_request=ip_request).values_list('text', flat=True)),
                         ['Configure 192.0.2.9/24 on sw8 eth1'])

        reserved = IPAddress(address='192.0.2.10/24')
        with self.captureOnCommitCallbacks(execute=True):
            request = fake_request(self.alice)
            token = current_request.set(request)
            try:
                gating.gate(reserved, ActionChoices.CREATE, request)
                reserved.save()
            finally:
                current_request.reset(token)
        self.assertFalse(WorkTask.objects.filter(change_request__object_id=reserved.pk,
                                                 change_request__content_type=ContentType.objects.get_for_model(IPAddress)).exists())

    def test_new_rack_has_no_task(self):
        rack = Rack(name='A9', site=self.site)
        with self.captureOnCommitCallbacks(execute=True):
            request = fake_request(self.alice)
            token = current_request.set(request)
            try:
                gating.gate(rack, ActionChoices.CREATE, request)
                rack.save()
            finally:
                current_request.reset(token)
        self.assertFalse(WorkTask.objects.exists())
