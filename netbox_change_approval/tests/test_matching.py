from django.test import TestCase

from core.models import ObjectType
from dcim.models import Manufacturer, Site
from tenancy.models import Tenant

from ..matching import rule_matches


class FakeRule:
    """Anything exposing .content_type_id / .content_type / .constraint matches the
    rule_matches() contract — avoids needing a DB-backed rule row for every case."""
    def __init__(self, content_type=None, constraint=None):
        self.content_type = content_type
        self.content_type_id = content_type.pk if content_type else None
        self.constraint = constraint


class RuleMatchesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant_acme = Tenant.objects.create(name='Acme', slug='acme')
        cls.tenant_other = Tenant.objects.create(name='Other', slug='other')
        cls.site_acme = Site.objects.create(name='Site A', slug='site-a', tenant=cls.tenant_acme)
        cls.site_other = Site.objects.create(name='Site B', slug='site-b', tenant=cls.tenant_other)

    def test_no_constraint_matches_everything(self):
        rule = FakeRule()
        self.assertTrue(rule_matches(rule, self.site_acme))
        self.assertTrue(rule_matches(rule, self.site_other))

    def test_content_type_mismatch_never_matches(self):
        rule = FakeRule(content_type=ObjectType.objects.get_for_model(Manufacturer))
        self.assertFalse(rule_matches(rule, self.site_acme))

    def test_saved_instance_constraint_via_db(self):
        rule = FakeRule(constraint={'tenant__slug': 'acme'})
        self.assertTrue(rule_matches(rule, self.site_acme))
        self.assertFalse(rule_matches(rule, self.site_other))

    def test_list_of_constraints_is_ored(self):
        rule = FakeRule(constraint=[{'tenant__slug': 'acme'}, {'tenant__slug': 'nope'}])
        self.assertTrue(rule_matches(rule, self.site_acme))
        self.assertFalse(rule_matches(rule, self.site_other))

    def test_unsaved_instance_uses_in_memory_lookup(self):
        rule = FakeRule(constraint={'tenant__slug': 'acme'})
        unsaved = Site(name='New Site', slug='new-site', tenant=self.tenant_acme)
        self.assertIsNone(unsaved.pk)
        self.assertTrue(rule_matches(rule, unsaved))

        unsaved_other = Site(name='New Site 2', slug='new-site-2', tenant=self.tenant_other)
        self.assertFalse(rule_matches(rule, unsaved_other))

    def test_unsaved_instance_isnull_lookup(self):
        rule = FakeRule(constraint={'tenant__isnull': True})
        unsaved = Site(name='No Tenant', slug='no-tenant')
        self.assertTrue(rule_matches(rule, unsaved))


class PendingManyToManyTests(TestCase):
    """New tags (and other many-to-many values) count as the new state."""

    def test_removing_tag_leaves_skip_rule_scope(self):
        from dcim.models import Manufacturer
        from extras.models import Tag

        from ..models import ApprovalRule, RuleOutcomeChoices

        lab = Tag.objects.create(name='Lab', slug='lab')
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.tags.add(lab)
        rule = ApprovalRule(outcome=RuleOutcomeChoices.SKIP, constraint={'tags__slug': 'lab'})

        obj = Manufacturer.objects.get(pk=obj.pk)
        obj._m2m_values = {'tags': []}
        self.assertFalse(rule.matches(obj, action='update'))

        obj._m2m_values = {'tags': [lab.pk]}  # pks, as from bulk edit's add/remove
        self.assertTrue(rule.matches(obj, action='update'))
