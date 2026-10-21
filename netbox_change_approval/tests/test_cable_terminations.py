from django.test import TestCase

from dcim.models import Cable, Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from users.models import Group, User

from .. import gating
from ..context import applying_change_on
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request


class CableTerminationTests(TestCase):
    """Re-pointing a cable end is a real change and needs approval."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.bob = User.objects.create(username='bob')
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Maker', slug='maker')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Box', slug='box')
        role = DeviceRole.objects.create(name='Role', slug='role')
        with applying_change_on():
            device = Device.objects.create(name='d1', site=site, device_type=device_type, role=role)
            cls.if1, cls.if2, cls.if3 = (
                Interface.objects.create(device=device, name=f'eth{i}', type='1000base-t')
                for i in range(3)
            )
            cls.cable = Cable(a_terminations=[cls.if1], b_terminations=[cls.if2])
            cls.cable.save()

    def _repoint(self):
        cable = Cable.objects.get(pk=self.cable.pk)
        cable.b_terminations = [self.if3]
        return cable

    def test_repointing_needs_approval(self):
        cable = self._repoint()
        change_request = gating.gate(cable, ActionChoices.UPDATE, fake_request(self.alice))

        self.assertIsInstance(change_request, ChangeRequest)
        self.assertEqual(
            change_request.diff['b_terminations'],
            {'old': [['dcim.interface', self.if2.pk]], 'new': [['dcim.interface', self.if3.pk]]},
        )
        self.assertNotIn('a_terminations', change_request.diff)

    def test_unchanged_terminations_are_not_a_change(self):
        cable = Cable.objects.get(pk=self.cable.pk)
        cable.a_terminations = [self.if1]
        cable.b_terminations = [self.if2]
        self.assertIsNone(gating.gate(cable, ActionChoices.UPDATE, fake_request(self.alice)))

    def test_approving_repoints_the_cable(self):
        change_request = gating.gate(self._repoint(), ActionChoices.UPDATE, fake_request(self.alice))
        change_request.apply(user=self.bob)

        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        cable = Cable.objects.get(pk=self.cable.pk)
        self.assertEqual([t.pk for t in cable.b_terminations], [self.if3.pk])
        self.assertEqual([t.pk for t in cable.a_terminations], [self.if1.pk])
