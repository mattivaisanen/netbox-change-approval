import uuid

from django.test import TestCase
from django.urls import reverse

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Rack, Site
from users.models import Group, User

from .. import gating
from ..context import applying_change_on
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request


class RejectWithDependentsTests(TestCase):
    """Rejecting new objects that depend on each other (a device in a new
    rack) removes them in an order that works, or says what's in the way."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.alice = User.objects.create(username='alice')
        with applying_change_on():
            cls.site = Site.objects.create(name='S', slug='s')
            mfr = Manufacturer.objects.create(name='M', slug='m')
            cls.device_type = DeviceType.objects.create(manufacturer=mfr, model='T', slug='t')
            cls.role = DeviceRole.objects.create(name='R', slug='r')

    def _create(self, obj, batch=None):
        request = fake_request(self.alice)
        gating.gate(obj, ActionChoices.CREATE, request)
        if batch:
            obj._nbca_pending_create.batch_id = batch
        obj.save()
        return ChangeRequest.objects.get(object_id=obj.pk, content_type__model=obj._meta.model_name)

    def _reject(self, change_request):
        self.client.force_login(self.approver)
        return self.client.post(
            reverse('plugins:netbox_change_approval:changerequest_reject', kwargs={'pk': change_request.pk}),
            {'comment': 'no', 'confirm': True},
        )

    def test_rejecting_batch_with_rack_and_its_device(self):
        batch = uuid.uuid4()
        rack_cr = self._create(Rack(site=self.site, name='Rack 1'), batch)
        device_cr = self._create(Device(
            site=self.site, rack=Rack.objects.get(name='Rack 1'), name='d1',
            device_type=self.device_type, role=self.role,
        ), batch)

        self._reject(rack_cr)

        for cr in (rack_cr, device_cr):
            cr.refresh_from_db()
            self.assertEqual(cr.status, StatusChoices.REJECTED)
        self.assertFalse(Rack.objects.filter(name='Rack 1').exists())
        self.assertFalse(Device.objects.filter(name='d1').exists())

    def test_rejecting_rack_used_by_approved_device_explains(self):
        rack_cr = self._create(Rack(site=self.site, name='Rack 2'))
        with applying_change_on():
            Device.objects.create(
                site=self.site, rack=Rack.objects.get(name='Rack 2'), name='d2',
                device_type=self.device_type, role=self.role,
            )

        response = self._reject(rack_cr)

        self.assertEqual(response.status_code, 302)
        rack_cr.refresh_from_db()
        self.assertEqual(rack_cr.status, StatusChoices.PENDING)
        self.assertTrue(Rack.objects.filter(name='Rack 2').exists())
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any('d2' in m for m in messages), messages)
