from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import Group, ObjectPermission, User

from .. import approvals
from ..models import ApprovalRule, ChangeRequest, RuleOutcomeChoices, StatusChoices


class DefaultRuleTests(TestCase):
    """The Default rule at the bottom of Approval Rules decides every change
    no other rule matches, and fills in blanks of "Needs approval" rules."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers, _created = Group.objects.get_or_create(name='Change Approvers')
        cls.board = Group.objects.create(name='Network Board')
        cls.admin = User.objects.create(username='admin', is_superuser=True)
        cls.alice = User.objects.create(username='alice')
        perm = ObjectPermission.objects.create(name='m', actions=['view', 'add', 'change', 'delete'])
        perm.object_types.set([ObjectType.objects.get_for_model(Manufacturer)])
        perm.users.set([cls.alice])

    def setUp(self):
        self.vendor = Manufacturer.objects.create(name='Acme', slug='acme')
        self.default = ApprovalRule.get_default()

    def _edit(self, message=''):
        self.client.force_login(self.alice)
        return self.client.post(reverse('dcim:manufacturer_edit', kwargs={'pk': self.vendor.pk}), {
            'name': 'Acme', 'slug': 'acme', 'description': 'changed', 'changelog_message': message, '_update': '',
        })

    def _url(self, name, **kwargs):
        return reverse(f'plugins:netbox_change_approval:approvalrule_{name}', kwargs=kwargs or None)

    def test_exactly_one_default_rule_listed_last(self):
        self.assertTrue(self.default.is_default)
        self.assertEqual(ApprovalRule.get_default().pk, self.default.pk)
        ApprovalRule.objects.create(priority=ApprovalRule.DEFAULT_PRIORITY, outcome=RuleOutcomeChoices.AUTO)
        self.assertEqual(list(ApprovalRule.objects.all())[-1], self.default)

    def test_recreated_when_missing(self):
        ApprovalRule.objects.filter(is_default=True).delete()
        rule = ApprovalRule.get_default()
        self.assertTrue(rule.is_default)
        self.assertEqual(rule.outcome, RuleOutcomeChoices.REQUIRE)

    def test_decides_changes_no_rule_matches(self):
        self.default.approver_group = self.board
        self.default.required_approvals = 2
        self.default.expire_after_days = 5
        self.default.save()
        self._edit('CHG-1')
        change_request = ChangeRequest.objects.get(object_id=self.vendor.pk, status=StatusChoices.PENDING)
        self.assertEqual(change_request.approver_group, self.board)
        self.assertEqual(change_request.required_approvals, 2)
        self.assertIsNotNone(change_request.expires_at)

    def test_default_rule_can_require_reason(self):
        self.default.require_reason = True
        self.default.save()
        response = self._edit('')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'asks for a reason')
        self.assertFalse(ChangeRequest.objects.exists())

    def test_matching_rule_wins_over_default(self):
        self.default.require_reason = True
        self.default.save()
        ApprovalRule.objects.create(
            priority=1, content_type=ObjectType.objects.get_for_model(Manufacturer),
            outcome=RuleOutcomeChoices.REQUIRE,
        )
        self._edit('')
        self.assertTrue(ChangeRequest.objects.filter(object_id=self.vendor.pk).exists())

    def test_blank_group_and_expiry_come_from_default_rule(self):
        self.default.approver_group = self.board
        self.default.expire_after_days = 3
        self.default.save()
        ApprovalRule.objects.create(
            priority=1, content_type=ObjectType.objects.get_for_model(Manufacturer),
            outcome=RuleOutcomeChoices.REQUIRE,
        )
        self.assertEqual(approvals.default_expiry_days(), 3)
        self._edit('x')
        change_request = ChangeRequest.objects.get(object_id=self.vendor.pk)
        self.assertEqual(change_request.approver_group, self.board)
        self.assertIsNotNone(change_request.expires_at)

    def test_cannot_be_deleted(self):
        self.client.force_login(self.admin)
        response = self.client.post(self._url('delete', pk=self.default.pk), {'confirm': True})
        self.assertEqual(response.status_code, 404)
        self.client.post(self._url('bulk_delete'), {'pk': [self.default.pk], 'confirm': True, '_confirm': True})
        self.assertTrue(ApprovalRule.objects.filter(pk=self.default.pk).exists())

    def test_edit_form_keeps_it_matching_everything(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._url('edit', pk=self.default.pk))
        self.assertNotContains(response, 'name="priority"')
        self.assertNotContains(response, 'name="outcome"')
        response = self.client.post(self._url('edit', pk=self.default.pk), {
            'description': 'Default rule', 'approver_group': self.board.pk, 'required_approvals': 1,
            'require_reason': 'on', 'expire_after_days': '', 'outcome': 'skip', 'enabled': '',
        })
        self.assertEqual(response.status_code, 302)
        self.default.refresh_from_db()
        self.assertEqual(self.default.approver_group, self.board)
        self.assertTrue(self.default.require_reason)
        self.assertEqual(self.default.outcome, RuleOutcomeChoices.REQUIRE)
        self.assertTrue(self.default.enabled)

    def test_list_and_detail_pages(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._url('list'))
        self.assertContains(response, 'Default rule')
        response = self.client.get(self.default.get_absolute_url())
        self.assertContains(response, 'Every change no other rule matches')
        self.assertNotContains(response, self._url('delete', pk=self.default.pk))
