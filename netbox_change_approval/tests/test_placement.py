from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from dcim.models import Device, DeviceRole, DeviceType, Location, Manufacturer, Rack, Site

from ..models import ActionChoices, ChangeRequest
from ..placement import placement_rows


class PlacementRowsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name='Tampere 1', slug='tampere-1')
        cls.location = Location.objects.create(name='Hall A', slug='hall-a', site=cls.site)
        cls.rack = Rack.objects.create(name='R4', site=cls.site, location=cls.location)
        cls.rack2 = Rack.objects.create(name='R5', site=cls.site, location=cls.location)
        manufacturer = Manufacturer.objects.create(name='Acme', slug='acme')
        cls.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Box', slug='box')
        cls.role = DeviceRole.objects.create(name='Leaf', slug='leaf')
        cls.device = Device.objects.create(
            name='sw1', device_type=cls.device_type, role=cls.role,
            site=cls.site, location=cls.location, rack=cls.rack, position=10, face='front',
        )
        cls.device_ct = ContentType.objects.get_for_model(Device)

    def _rows(self, **kwargs):
        cr = ChangeRequest(content_type=self.device_ct, object_repr='x', **kwargs)
        return {row['label']: row for row in placement_rows(cr)}

    def test_create_uses_proposed_data(self):
        rows = self._rows(action=ActionChoices.CREATE, diff={
            'name': 'new', 'device_type_id': self.device_type.pk, 'role_id': self.role.pk,
            'site_id': self.site.pk, 'location_id': self.location.pk, 'rack_id': self.rack.pk,
            'position': '37.0', 'face': 'front',
        })
        self.assertEqual(list(rows), ['Site', 'Location', 'Rack', 'Position (U)', 'Rack face'])
        self.assertEqual(rows['Site']['value'], self.site)
        self.assertEqual(rows['Location']['value'], self.location)
        self.assertEqual(rows['Rack']['value'], self.rack)
        self.assertEqual(rows['Position (U)']['value'], '37')
        self.assertEqual(rows['Rack face']['value'], 'Front')

    def test_create_skips_unset_fields(self):
        rows = self._rows(action=ActionChoices.CREATE, diff={'site_id': self.site.pk})
        self.assertEqual(list(rows), ['Site'])

    def test_update_reads_object_and_shows_moves(self):
        rows = self._rows(action=ActionChoices.UPDATE, object_id=self.device.pk, diff={
            'rack_id': {'old': self.rack.pk, 'new': self.rack2.pk},
            'position': {'old': '10.0', 'new': '12.5'},
        })
        self.assertFalse(rows['Site']['changed'])
        self.assertEqual(rows['Site']['value'], self.site)
        self.assertTrue(rows['Rack']['changed'])
        self.assertEqual((rows['Rack']['value'], rows['Rack']['new']), (self.rack, self.rack2))
        self.assertEqual((rows['Position (U)']['value'], rows['Position (U)']['new']), ('10', '12.5'))

    def test_delete_of_already_removed_object_uses_snapshot(self):
        rows = self._rows(action=ActionChoices.DELETE, object_id=999999, diff={'site_id': self.site.pk})
        self.assertEqual(rows['Site']['value'], self.site)

    def test_missing_related_object(self):
        rows = self._rows(action=ActionChoices.CREATE, diff={'rack_id': 999999})
        self.assertEqual(rows['Rack']['value'], '#999999 (not found)')

    def test_generic_fk_scope(self):
        from ipam.models import Prefix

        ct = ContentType.objects.get_for_model(Prefix)
        cr = ChangeRequest(content_type=ct, object_repr='x', action=ActionChoices.CREATE, diff={
            'prefix': '10.0.0.0/24',
            'scope_type_id': ContentType.objects.get_for_model(Site).pk,
            'scope_id': self.site.pk,
        })
        rows = {row['label']: row for row in placement_rows(cr)}
        self.assertEqual(rows['Scope']['value'], self.site)
