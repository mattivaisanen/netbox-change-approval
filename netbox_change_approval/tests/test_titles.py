from django.test import TestCase

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Rack, Site
from users.models import User

from .. import gating
from ..context import applying_change_on
from ..models import ActionChoices, ChangeRequest
from ..titles import attach_titles
from .utils import fake_request


class TitleTests(TestCase):
    """List titles say what is done to what: "Create Rack R1",
    "Edit Device Access switch sw1"."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username='alice')
        with applying_change_on():
            cls.site = Site.objects.create(name='S', slug='s')
            mfr = Manufacturer.objects.create(name='M', slug='m')
            cls.device_type = DeviceType.objects.create(manufacturer=mfr, model='T', slug='t')
            cls.role = DeviceRole.objects.create(name='Access switch', slug='access')
            cls.device = Device.objects.create(name='sw1', site=cls.site, device_type=cls.device_type, role=cls.role)

    def test_create_rack(self):
        rack = Rack(site=self.site, name='R1')
        gating.gate(rack, ActionChoices.CREATE, fake_request(self.alice))
        rack.save()
        cr = attach_titles([ChangeRequest.objects.get(object_id=rack.pk, action='create')])[0]
        self.assertEqual(cr.nbca_title, 'Create Rack R1')

    def test_edit_device_has_role(self):
        device = Device.objects.get(pk=self.device.pk)
        device.description = 'new'
        cr = gating.gate(device, ActionChoices.UPDATE, fake_request(self.alice))
        self.assertEqual(attach_titles([cr])[0].nbca_title, 'Edit Device Access switch sw1')
