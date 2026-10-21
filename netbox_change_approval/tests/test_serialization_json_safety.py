from django.test import TestCase

from dcim.models import DeviceType, Manufacturer

from .. import serialization

# Regression test for a bug caught by live deployment, not by unit tests: Device/
# DeviceType creation crashed with "TypeError: Object of type ImageFieldFile is not
# JSON serializable" the first time a real user tried it, because snapshot_create()
# was dumping raw field attributes (including ImageField/FileField values, which are
# FieldFile wrapper objects, not plain strings) straight into a dict destined for a
# JSONField. DjangoJSONEncoder handles Decimal/date/datetime/UUID but has no idea
# what a FieldFile is.


class JsonSafetyTests(TestCase):
    def test_imagefield_does_not_crash_snapshot_create(self):
        mfr = Manufacturer.objects.create(name='Acme', slug='acme')
        unsaved = DeviceType(manufacturer=mfr, model='Widget 9000', slug='widget-9000', u_height=1)

        diff = serialization.snapshot_create(unsaved)  # must not raise

        # Must survive an actual JSON round-trip (what the DB layer will do),
        # not just "didn't raise in this function".
        import json
        from django.core.serializers.json import DjangoJSONEncoder
        json.dumps(diff, cls=DjangoJSONEncoder)

        # Untouched (default-valued, '') image fields are correctly left out of
        # the diff entirely now (see snapshot_create's docstring) — the
        # regression this test guards is the crash above, not these being present.
        self.assertNotIn('front_image', diff)
        self.assertNotIn('rear_image', diff)
        self.assertEqual(diff['model'], 'Widget 9000')
