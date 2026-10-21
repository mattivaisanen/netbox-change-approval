from django.test import TestCase

from core.models import ObjectType
from dcim.models import Manufacturer

from .. import serialization
from ..context import applying_change_on

# apply_update/apply_delete call full_clean()/delete() themselves, which is
# exactly what re-triggers our own gate (see validators.py / signals.py). In
# production these are only ever called from within ChangeRequest.apply(), which
# wraps them in applying_change_on() for that reason — these tests do the same, to
# exercise the functions the way they're actually used rather than in a way that
# would otherwise recursively queue a *second* pending change for every call.


class SerializationRoundTripTests(TestCase):
    def test_snapshot_create_captures_set_fields_only(self):
        # No apply_create() round-trip here: a CREATE's object is written for
        # real immediately (tagged "Pending Approval") rather than reconstructed
        # later from this snapshot — see gating.gate()'s docstring. This snapshot
        # is purely the audit/diff record shown to approvers, which is exactly
        # why fields left at their default (e.g. description='') are omitted.
        unsaved = Manufacturer(name='Acme Corp', slug='acme-corp', description='Widgets')

        diff = serialization.snapshot_create(unsaved)

        self.assertEqual(diff['name'], 'Acme Corp')
        self.assertEqual(diff['slug'], 'acme-corp')
        self.assertEqual(diff['description'], 'Widgets')
        self.assertFalse(Manufacturer.objects.filter(slug='acme-corp').exists())

    def test_snapshot_update_only_includes_changed_fields(self):
        old = Manufacturer.objects.create(name='Acme Corp', slug='acme-corp', description='Widgets')
        new = Manufacturer.objects.get(pk=old.pk)
        new.description = 'Widgets and gadgets'

        diff = serialization.snapshot_update(old, new)

        self.assertEqual(set(diff.keys()), {'description'})
        self.assertEqual(diff['description'], {'old': 'Widgets', 'new': 'Widgets and gadgets'})

    def test_snapshot_update_empty_when_nothing_changed(self):
        old = Manufacturer.objects.create(name='Acme Corp', slug='acme-corp')
        new = Manufacturer.objects.get(pk=old.pk)

        self.assertEqual(serialization.snapshot_update(old, new), {})

    def test_apply_update(self):
        obj = Manufacturer.objects.create(name='Acme Corp', slug='acme-corp', description='Widgets')
        content_type = ObjectType.objects.get_for_model(Manufacturer)
        diff = {'description': {'old': 'Widgets', 'new': 'Widgets and gadgets'}}

        with applying_change_on():
            serialization.apply_update(content_type, obj.pk, diff)

        obj.refresh_from_db()
        self.assertEqual(obj.description, 'Widgets and gadgets')

    def test_apply_delete(self):
        obj = Manufacturer.objects.create(name='Acme Corp', slug='acme-corp')
        content_type = ObjectType.objects.get_for_model(Manufacturer)

        with applying_change_on():
            serialization.apply_delete(content_type, obj.pk)

        self.assertFalse(Manufacturer.objects.filter(pk=obj.pk).exists())
