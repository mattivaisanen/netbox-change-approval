from django.test import TestCase

from core.models import ObjectType
from dcim.models import Cable, Device, DeviceRole, DeviceType, Interface, Manufacturer, Region, Site
from users.models import User

from .. import gating
from ..models import ActionChoices, ChangeRequest, ExemptModel
from .utils import fake_request

TAMPERE_CABLES = [
    {'a_terminations__device__site__region__slug': 'tampere'},
    {'b_terminations__device__site__region__slug': 'tampere'},
]


class ExemptConstraintTests(TestCase):
    """Exempt Models with a constraint: e.g. cables in the Tampere region skip approval."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username='alice')
        tampere = Region.objects.create(name='Tampere', slug='tampere')
        helsinki = Region.objects.create(name='Helsinki', slug='helsinki')
        site_tre = Site.objects.create(name='TRE1', slug='tre1', region=tampere)
        site_hki = Site.objects.create(name='HKI1', slug='hki1', region=helsinki)
        manufacturer = Manufacturer.objects.create(name='M', slug='m')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='T', slug='t')
        role = DeviceRole.objects.create(name='R', slug='r')
        tre = Device.objects.create(name='tre-sw', site=site_tre, device_type=device_type, role=role)
        hki = Device.objects.create(name='hki-sw', site=site_hki, device_type=device_type, role=role)
        cls.tre_if = [Interface.objects.create(device=tre, name=f'eth{i}', type='1000base-t') for i in range(4)]
        cls.hki_if = [Interface.objects.create(device=hki, name=f'eth{i}', type='1000base-t') for i in range(4)]
        cls.cable_ct = ObjectType.objects.get_for_model(Cable)
        ExemptModel.objects.create(content_type=cls.cable_ct, constraint=TAMPERE_CABLES)

    def test_new_cable_in_tampere_is_exempt(self):
        cable = Cable(a_terminations=[self.tre_if[0]], b_terminations=[self.tre_if[1]])

        self.assertIsNone(gating.gate(cable, ActionChoices.CREATE, fake_request(self.alice)))
        self.assertFalse(hasattr(cable, '_nbca_pending_create'))

    def test_new_cable_with_one_end_in_tampere_is_exempt(self):
        cable = Cable(a_terminations=[self.hki_if[0]], b_terminations=[self.tre_if[1]])

        gating.gate(cable, ActionChoices.CREATE, fake_request(self.alice))
        self.assertFalse(hasattr(cable, '_nbca_pending_create'))

    def test_new_cable_in_helsinki_is_gated(self):
        cable = Cable(a_terminations=[self.hki_if[0]], b_terminations=[self.hki_if[1]])

        gating.gate(cable, ActionChoices.CREATE, fake_request(self.alice))
        self.assertTrue(hasattr(cable, '_nbca_pending_create'))

    def test_editing_existing_tampere_cable_is_exempt(self):
        cable = Cable(a_terminations=[self.tre_if[2]], b_terminations=[self.tre_if[3]])
        cable.save()  # no request: not gated
        cable = Cable.objects.get(pk=cable.pk)
        cable.label = 'patched'

        self.assertIsNone(gating.gate(cable, ActionChoices.UPDATE, fake_request(self.alice)))
        self.assertFalse(ChangeRequest.objects.filter(object_id=cable.pk).exists())

    def test_moving_cable_out_of_tampere_is_gated(self):
        cable = Cable(a_terminations=[self.tre_if[2]], b_terminations=[self.tre_if[3]])
        cable.save()
        cable = Cable.objects.get(pk=cable.pk)
        cable.a_terminations = [self.hki_if[2]]
        cable.b_terminations = [self.hki_if[3]]

        self.assertFalse(gating._is_exempt(cable, self.cable_ct, ActionChoices.UPDATE))

    def test_unconstrained_row_still_exempts_everything(self):
        ct = ObjectType.objects.get_for_model(Manufacturer)
        ExemptModel.objects.create(content_type=ct)

        self.assertTrue(gating._is_exempt(Manufacturer(name='X', slug='x'), ct, ActionChoices.CREATE))
