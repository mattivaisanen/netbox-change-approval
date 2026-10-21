from decimal import Decimal

from django.test import TestCase

from core.models import ObjectType
from dcim.models import Manufacturer, Site

from .. import serialization
from ..context import applying_change_on
from ..exceptions import ChangeConflict
from ..models import ActionChoices, ChangeRequest, StatusChoices


class ApplyConflictTests(TestCase):
    """An approved update must never overwrite an edit made after it was submitted."""

    def setUp(self):
        self.obj = Manufacturer.objects.create(name='Acme', slug='acme', description='Widgets')
        self.ct = ObjectType.objects.get_for_model(Manufacturer)
        self.diff = {'description': {'old': 'Widgets', 'new': 'Gadgets'}}

    def test_apply_refuses_when_field_changed_since_submission(self):
        # Someone (API, script, bypass user) edits the same field meanwhile.
        Manufacturer.objects.filter(pk=self.obj.pk).update(description='Newer edit')

        with applying_change_on(), self.assertRaises(ChangeConflict) as ctx:
            serialization.apply_update(self.ct, self.obj.pk, self.diff)

        self.assertEqual(ctx.exception.fields, ['description'])
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.description, 'Newer edit')

    def test_unrelated_field_change_is_not_a_conflict(self):
        Manufacturer.objects.filter(pk=self.obj.pk).update(name='Acme Inc')

        with applying_change_on():
            serialization.apply_update(self.ct, self.obj.pk, self.diff)

        self.obj.refresh_from_db()
        self.assertEqual(self.obj.description, 'Gadgets')
        self.assertEqual(self.obj.name, 'Acme Inc')  # newer edit kept

    def test_change_request_stays_pending_on_conflict(self):
        cr = ChangeRequest.objects.create(
            content_type=self.ct, object_id=self.obj.pk, object_repr=str(self.obj),
            action=ActionChoices.UPDATE, diff=self.diff,
        )
        Manufacturer.objects.filter(pk=self.obj.pk).update(description='Newer edit')

        with self.assertRaises(ChangeConflict):
            cr.apply()

        cr.refresh_from_db()
        self.assertEqual(cr.status, StatusChoices.PENDING)
        self.assertIsNone(cr.applied_at)

    def test_missing_object_is_a_conflict(self):
        pk = self.obj.pk
        with applying_change_on():  # delete it directly, without queueing approval
            Manufacturer.objects.filter(pk=pk).delete()

        with applying_change_on(), self.assertRaises(ChangeConflict) as ctx:
            serialization.apply_update(self.ct, pk, self.diff)
        self.assertTrue(ctx.exception.missing)

    def test_decimal_old_value_matches_in_memory_and_round_tripped(self):
        # gating.gate()'s auto-approval path applies a diff still holding real
        # Decimals; a diff loaded back from the DB holds strings. Neither may be
        # reported as a conflict when nothing actually changed.
        site = Site.objects.create(name='S1', slug='s1', latitude=Decimal('60.123456'))
        site.refresh_from_db()
        ct = ObjectType.objects.get_for_model(Site)

        with applying_change_on():
            serialization.apply_update(ct, site.pk, {'latitude': {'old': site.latitude, 'new': Decimal('61.000000')}})
        site.refresh_from_db()
        self.assertEqual(site.latitude, Decimal('61.000000'))

        cr = ChangeRequest.objects.create(
            content_type=ct, object_id=site.pk, object_repr=str(site), action=ActionChoices.UPDATE,
            diff={'latitude': {'old': site.latitude, 'new': Decimal('62.000000')}},
        )
        cr.refresh_from_db()  # diff now JSON round-tripped
        cr.apply()
        site.refresh_from_db()
        self.assertEqual(site.latitude, Decimal('62.000000'))

    def test_conflicts_for_reports_fields_without_writing(self):
        Manufacturer.objects.filter(pk=self.obj.pk).update(description='Newer edit')

        fields, missing = serialization.conflicts_for(self.ct, self.obj.pk, self.diff)

        self.assertEqual(fields, ['description'])
        self.assertFalse(missing)
