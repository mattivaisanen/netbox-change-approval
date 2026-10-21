from django.test import TestCase

from core.models import ObjectType
from dcim.models import Manufacturer, Site
from users.models import Group, User

from .. import gating
from ..matching import rule_matches
from ..models import ActionChoices, ApprovalRule, AutoApprovalActionChoices, ChangeRequest, StatusChoices
from .test_matching import FakeRule
from .utils import fake_request


class NewStateMatchingTests(TestCase):
    """rule_matches(check_new_state=True): stored *and* submitted values must match."""

    def setUp(self):
        self.site = Site.objects.create(name='S1', slug='s1', status='planned')
        self.rule = FakeRule(constraint={'status': 'planned'})

    def test_edit_that_stays_planned_matches(self):
        self.site.description = 'moved rack plan'
        self.assertTrue(rule_matches(self.rule, self.site, check_new_state=True))

    def test_planned_to_active_does_not_match(self):
        self.site.status = 'active'
        self.assertFalse(rule_matches(self.rule, self.site, check_new_state=True))
        # Without the new-state check, only the stored row is looked at.
        self.assertTrue(rule_matches(self.rule, self.site))

    def test_active_to_planned_does_not_match(self):
        site = Site.objects.create(name='S2', slug='s2', status='active')
        site.status = 'planned'
        self.assertFalse(rule_matches(self.rule, site, check_new_state=True))

    def test_constraint_on_missing_field_is_no_match_not_crash(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'x'
        self.assertFalse(rule_matches(self.rule, obj, check_new_state=True))
        self.assertFalse(rule_matches(self.rule, obj))


class PlannedAutoApprovalTests(TestCase):
    """README example: create and edit planned objects freely; promoting needs approval."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        ApprovalRule.objects.create(
            outcome='auto',
            content_type=ObjectType.objects.get_for_model(Site),
            constraint={'status': 'planned'},
            action=AutoApprovalActionChoices.ANY,
            description='planned sites',
        )

    def test_editing_planned_site_is_auto_approved(self):
        site = Site.objects.create(name='S1', slug='s1', status='planned')
        site.description = 'new description'

        # Auto-approved: gate() logs the request and lets the save proceed.
        self.assertIsNone(gating.gate(site, ActionChoices.UPDATE, fake_request(self.alice)))
        site.save()

        change_request = ChangeRequest.objects.get(object_id=site.pk)
        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertTrue(change_request.auto_approved)
        site.refresh_from_db()
        self.assertEqual(site.description, 'new description')

    def test_planned_to_active_needs_approval(self):
        site = Site.objects.create(name='S1', slug='s1', status='planned')
        site.status = 'active'

        change_request = gating.gate(site, ActionChoices.UPDATE, fake_request(self.alice))

        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertFalse(change_request.auto_approved)
        site.refresh_from_db()
        self.assertEqual(site.status, 'planned')
        self.assertTrue(ChangeRequest.objects.filter(object_id=site.pk, status=StatusChoices.PENDING).exists())
