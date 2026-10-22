import importlib
import uuid

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection
from django.test import TestCase

from core.models import ObjectChange

cleanup = importlib.import_module('netbox_change_approval.migrations.0009_remove_legacy_rule_history')


class LegacyRuleHistoryCleanupTests(TestCase):
    """Upgrading from a version with the old rule tables: their change log
    entries must not stop remove_stale_contenttypes (netbox-docker runs it on
    every start, and a failure there stops NetBox from starting)."""

    def setUp(self):
        self.stale_types = [
            # get_or_create: a kept test database may still hold them.
            ContentType.objects.get_or_create(app_label='netbox_change_approval', model=name)[0]
            for name in cleanup.LEGACY_MODELS
        ]
        for ct in self.stale_types:
            ObjectChange.objects.create(
                changed_object_type=ct, changed_object_id=1, action='create',
                object_repr='Old rule', user_name='admin', request_id=uuid.uuid4(),
            )

    def test_stale_types_block_removal_without_the_cleanup(self):
        with self.assertRaises(Exception):
            call_command('remove_stale_contenttypes', interactive=False, verbosity=0)

    def test_stale_types_are_removed_after_the_cleanup(self):
        cleanup.remove_legacy_rule_history(apps, connection.schema_editor())
        call_command('remove_stale_contenttypes', interactive=False, verbosity=0)

        self.assertFalse(ContentType.objects.filter(
            app_label='netbox_change_approval', model__in=cleanup.LEGACY_MODELS,
        ).exists())
        self.assertFalse(ObjectChange.objects.filter(changed_object_type__in=self.stale_types).exists())
        # The plugin's current types are untouched.
        self.assertTrue(ContentType.objects.filter(
            app_label='netbox_change_approval', model='approvalrule',
        ).exists())
