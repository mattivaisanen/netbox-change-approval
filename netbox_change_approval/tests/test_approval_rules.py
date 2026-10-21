from django.test import TestCase

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import Group, User

from .. import gating
from ..models import ActionChoices, ApprovalRule, ChangeRequest, StatusChoices
from .utils import fake_request


class ApprovalRuleOrderTests(TestCase):
    """One rule list: the first enabled match (by priority) decides."""

    @classmethod
    def setUpTestData(cls):
        cls.default_group = Group.objects.create(name='Change Approvers')
        cls.hardware = Group.objects.create(name='Hardware')
        cls.alice = User.objects.create(username='alice')
        cls.ct = ObjectType.objects.get_for_model(Manufacturer)

    def _update(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'edit'
        return obj, gating.gate(obj, ActionChoices.UPDATE, fake_request(self.alice))

    def test_needs_approval_rule_before_auto_rule_wins(self):
        ApprovalRule.objects.create(priority=10, content_type=self.ct, outcome='require', approver_group=self.hardware)
        ApprovalRule.objects.create(priority=20, content_type=self.ct, outcome='auto')

        _obj, change_request = self._update()

        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(change_request.approver_group, self.hardware)

    def test_auto_rule_before_needs_approval_rule_wins(self):
        ApprovalRule.objects.create(priority=10, content_type=self.ct, outcome='auto')
        ApprovalRule.objects.create(priority=20, content_type=self.ct, outcome='require', approver_group=self.hardware)

        obj, result = self._update()

        self.assertIsNone(result)
        self.assertEqual(ChangeRequest.objects.get(object_id=obj.pk).status, StatusChoices.APPLIED)

    def test_needs_approval_without_group_uses_default(self):
        ApprovalRule.objects.create(priority=10, content_type=self.ct, outcome='require')

        _obj, change_request = self._update()

        self.assertEqual(change_request.approver_group, self.default_group)

    def test_disabled_rule_is_ignored(self):
        ApprovalRule.objects.create(priority=10, content_type=self.ct, outcome='skip', enabled=False)

        _obj, change_request = self._update()

        self.assertEqual(change_request.status, StatusChoices.PENDING)

    def test_skip_leaves_no_record(self):
        ApprovalRule.objects.create(priority=10, content_type=self.ct, outcome='skip')

        obj, result = self._update()

        self.assertIsNone(result)
        self.assertFalse(ChangeRequest.objects.filter(object_id=obj.pk).exists())
