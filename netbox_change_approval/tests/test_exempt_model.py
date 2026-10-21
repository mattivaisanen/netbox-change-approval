from django.test import TestCase

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import User

from .. import gating
from ..models import ActionChoices, ApprovalRule, ChangeRequest
from .utils import fake_request


class ExemptModelGatingTests(TestCase):
    """
    "Skip approval" rules are checked live inside gate(), not at
    process-start registration time like PLUGINS_CONFIG['exempt_models'] — so
    toggling `enabled` here must take effect on the very next call, no restart,
    which is the whole point of this table over the static config list.
    """
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username='alice')
        cls.content_type = ObjectType.objects.get_for_model(Manufacturer)

    def test_enabled_exemption_skips_gating_entirely(self):
        ApprovalRule.objects.create(content_type=self.content_type, outcome='skip', enabled=True)
        unsaved = Manufacturer(name='Acme', slug='acme')

        result = gating.gate(unsaved, ActionChoices.CREATE, fake_request(self.alice))

        self.assertIsNone(result)
        self.assertFalse(hasattr(unsaved, '_nbca_pending_create'))
        unsaved.save()
        self.assertFalse(ChangeRequest.objects.filter(object_id=unsaved.pk, status='pending').exists())
        self.assertFalse(ChangeRequest.objects.filter(object_id=unsaved.pk).exists())

    def test_disabled_exemption_does_not_skip_gating(self):
        ApprovalRule.objects.create(content_type=self.content_type, outcome='skip', enabled=False)
        unsaved = Manufacturer(name='Acme', slug='acme')

        result = gating.gate(unsaved, ActionChoices.CREATE, fake_request(self.alice))

        self.assertIsNone(result)  # CREATE always returns None — see gating.gate()'s docstring
        self.assertTrue(hasattr(unsaved, '_nbca_pending_create'))  # ...but it IS still being gated
        unsaved.save()
        self.assertTrue(ChangeRequest.objects.filter(object_id=unsaved.pk, status='pending').exists())

    def test_no_exemption_row_gates_normally(self):
        unsaved = Manufacturer(name='Acme', slug='acme')

        result = gating.gate(unsaved, ActionChoices.CREATE, fake_request(self.alice))

        self.assertIsNone(result)
        self.assertTrue(hasattr(unsaved, '_nbca_pending_create'))

    def test_toggling_enabled_takes_effect_immediately(self):
        exemption = ApprovalRule.objects.create(content_type=self.content_type, outcome='skip', enabled=True)
        gated = Manufacturer(name='Gated', slug='gated')
        gating.gate(gated, ActionChoices.CREATE, fake_request(self.alice))
        self.assertFalse(hasattr(gated, '_nbca_pending_create'))  # exempt: not gated

        exemption.enabled = False
        exemption.save()

        ungated = Manufacturer(name='Ungated', slug='ungated')
        gating.gate(ungated, ActionChoices.CREATE, fake_request(self.alice))
        self.assertTrue(hasattr(ungated, '_nbca_pending_create'))  # no longer exempt: gated again
