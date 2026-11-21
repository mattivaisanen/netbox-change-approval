from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import ConfigRevision
from dcim.models import Manufacturer
from netbox.config import clear_config
from netbox.context import current_request
from users.models import User

from ..exceptions import ChangeApprovalRequired
from .utils import fake_request


class ConfigRevisionValidatorTests(TestCase):
    """CUSTOM_VALIDATORS from a Configuration Revision run next to the
    plugin's own validator (the plugin no longer writes the setting)."""

    def setUp(self):
        self.alice = User.objects.create(username='alice')
        self.existing = Manufacturer.objects.create(name='Existing', slug='existing')
        token = current_request.set(fake_request(self.alice))
        self.addCleanup(current_request.reset, token)
        self.addCleanup(self._reset_config)

    @staticmethod
    def _reset_config():
        cache.delete_many(['config', 'config_version'])
        clear_config()

    def _activate_revision(self, validators):
        revision = ConfigRevision.objects.create(data={'CUSTOM_VALIDATORS': validators})
        revision.activate()
        clear_config()

    def test_plugin_leaves_custom_validators_setting_alone(self):
        self.assertFalse(hasattr(settings, 'CUSTOM_VALIDATORS'))

    def test_revision_validator_runs(self):
        self._activate_revision({'dcim.manufacturer': [{'description': {'max_length': 5}}]})
        obj = Manufacturer(name='M', slug='m', description='far too long')

        with self.assertRaises(ValidationError):
            obj.full_clean()

    def test_plugin_validator_still_runs_with_revision_validators(self):
        self._activate_revision({'dcim.manufacturer': [{'description': {'max_length': 50}}]})
        obj = Manufacturer.objects.get(pk=self.existing.pk)
        obj.description = 'pending edit'

        # The revision's validator passes; the plugin's queues the update.
        with self.assertRaises(ChangeApprovalRequired):
            obj.full_clean()
