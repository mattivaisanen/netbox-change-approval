import json
import re

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Rack, Site
from users.models import User

from ..models import ActionChoices, ChangeRequest

PLUGINS_CONFIG_OUTLINE = {
    'netbox_change_approval': {
        'default_approver_group': 'Change Approvers',
        'exempt_models': [],
        'pending_row_style': 'outline',
    },
}


class PendingMarksTests(TestCase):
    """Pending rows in object lists and rack elevations show what's pending:
    new, edit or delete."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(username='admin', is_superuser=True)
        ct = ContentType.objects.get_for_model(Manufacturer)
        cls.new = Manufacturer.objects.create(name='Newco', slug='newco')
        cls.edited = Manufacturer.objects.create(name='Editco', slug='editco', description='old text')
        cls.doomed = Manufacturer.objects.create(name='Goneco', slug='goneco')
        cls.plain = Manufacturer.objects.create(name='Plainco', slug='plainco')
        for obj, action, diff in (
            (cls.new, ActionChoices.CREATE, {'name': 'Newco'}),
            (cls.edited, ActionChoices.UPDATE, {'description': {'old': 'old text', 'new': 'new text'}}),
            (cls.doomed, ActionChoices.DELETE, {}),
        ):
            ChangeRequest.objects.create(
                content_type=ct, object_id=obj.pk, object_repr=str(obj), action=action, diff=diff,
                submitted_by=cls.admin,
            )

    def _rows(self, html):
        return {
            int(m.group(2)): m.group(1)
            for m in re.finditer(r'<tr([^>]*)>\s*<td[^>]*><input type="checkbox" name="pk" value="(\d+)"', html)
        }

    def _list(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dcim:manufacturer_list'))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_rows_marked_by_action(self):
        rows = self._rows(self._list())
        self.assertIn('nbca-pending-create', rows[self.new.pk])
        self.assertIn('data-nbca-action="create"', rows[self.new.pk])
        self.assertIn('nbca-pending-update', rows[self.edited.pk])
        self.assertIn('nbca-pending-delete', rows[self.doomed.pk])
        self.assertNotIn('nbca-pending', rows[self.plain.pk])

    def test_edit_row_carries_requested_values(self):
        html = self._list()
        row = self._rows(html)[self.edited.pk]
        changes = json.loads(re.search(r'data-nbca-changes="([^"]*)"', row).group(1).replace('&quot;', '"'))
        self.assertEqual(changes, {'description': 'new text'})
        self.assertIn('data-nbca-col="description"', html)

    def test_badge_counts_each_kind(self):
        html = self._list()
        self.assertIn('3 pending approvals', html)
        for label in ('1 new', '1 edit', '1 delete'):
            self.assertIn(label, html)
        self.assertIn(f'?id={self.doomed.pk}', html)

    @override_settings(PLUGINS_CONFIG=PLUGINS_CONFIG_OUTLINE)
    def test_outline_setting_restores_old_look(self):
        html = self._list()
        rows = self._rows(html)
        self.assertIn('nbca-pending-row', rows[self.edited.pk])
        self.assertNotIn('nbca-pending-update', rows[self.edited.pk])
        self.assertNotIn('data-nbca-action="', html)
        self.assertNotIn('1 edit', html)

    def test_rack_elevation_colors_by_action(self):
        site = Site.objects.create(name='S1', slug='s1')
        rack = Rack.objects.create(name='R1', site=site)
        device_type = DeviceType.objects.create(manufacturer=self.plain, model='Box', slug='box')
        role = DeviceRole.objects.create(name='Role', slug='role')
        device = Device.objects.create(
            name='sw1', site=site, rack=rack, position=1, face='front', device_type=device_type, role=role,
        )
        ChangeRequest.objects.create(
            content_type=ContentType.objects.get_for_model(Device), object_id=device.pk, object_repr='sw1',
            action=ActionChoices.DELETE, submitted_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(f'/api/dcim/racks/{rack.pk}/elevation/?render=svg&face=front')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'nbca-pending-delete', response.content)
