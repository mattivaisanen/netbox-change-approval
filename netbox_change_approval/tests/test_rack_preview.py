from django.test import TestCase

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Rack, Site
from users.models import Group, User

from .. import gating, placement
from ..models import ActionChoices
from .utils import fake_request


class RackPreviewTests(TestCase):
    """The request page shows the rack only when a device is put in, taken
    out of, or moved within a rack; the elevation marks where it would go."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.get_or_create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.admin = User.objects.create(username='admin', is_superuser=True)
        cls.site = Site.objects.create(name='S1', slug='s1')
        cls.rack1 = Rack.objects.create(name='R1', site=cls.site)
        cls.rack2 = Rack.objects.create(name='R2', site=cls.site)
        manufacturer = Manufacturer.objects.create(name='M', slug='m')
        cls.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Box', slug='box')
        cls.role = DeviceRole.objects.create(name='R', slug='r')

    def setUp(self):
        self.device = Device.objects.create(
            name='sw1', site=self.site, rack=self.rack1, position=10, face='front',
            device_type=self.device_type, role=self.role,
        )

    def _update(self, **changes):
        device = Device.objects.get(pk=self.device.pk)
        for key, value in changes.items():
            setattr(device, key, value)
        return gating.gate(device, ActionChoices.UPDATE, fake_request(self.alice))

    def test_rename_has_no_rack_preview(self):
        self.assertEqual(placement.rack_previews(self._update(name='sw1-new')), [])

    def test_move_to_other_rack_shows_both(self):
        previews = placement.rack_previews(self._update(rack=self.rack2, position=20))
        self.assertEqual([(rack, caption) for rack, _face, caption in previews],
                         [(self.rack1, 'Now'), (self.rack2, 'Requested')])

    def test_move_within_rack_shows_one(self):
        previews = placement.rack_previews(self._update(position=20))
        self.assertEqual(len(previews), 1)

    def test_delete_from_rack_shows_rack(self):
        from ..models import ChangeRequest
        from django.contrib.contenttypes.models import ContentType

        change_request = ChangeRequest.objects.create(
            content_type=ContentType.objects.get_for_model(Device), object_id=self.device.pk,
            object_repr='sw1', action=ActionChoices.DELETE, submitted_by=self.alice,
        )
        self.assertEqual(placement.rack_previews(change_request)[0][0], self.rack1)

    def test_page_and_elevation_show_requested_spot(self):
        change_request = self._update(rack=self.rack2, position=20)
        self.client.force_login(self.admin)
        page = self.client.get(change_request.get_absolute_url())
        self.assertContains(page, f'/api/dcim/racks/{self.rack2.pk}/elevation/')
        svg = self.client.get(f'/api/dcim/racks/{self.rack2.pk}/elevation/?face=front&render=svg')
        self.assertIn(b'nbca-requested-move', svg.content)
        self.assertIn(b'sw1 (requested)', svg.content)
