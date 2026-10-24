from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from users.models import ObjectPermission, User

from ..models import ApprovalRule, RuleOutcomeChoices


class RuleAccessTests(TestCase):
    """Only superusers can add, change or delete approval rules, even when a
    NetBox permission grants more."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create(username='staff')
        perm = ObjectPermission.objects.create(
            name='all-rules', enabled=True, actions=['view', 'add', 'change', 'delete'],
        )
        perm.object_types.set([ObjectType.objects.get_for_model(ApprovalRule)])
        perm.users.set([cls.staff])
        cls.admin = User.objects.create(username='admin', is_superuser=True)
        cls.rule = ApprovalRule.objects.create(priority=10, outcome=RuleOutcomeChoices.REQUIRE)

    def _url(self, name, **kwargs):
        return reverse(f'plugins:netbox_change_approval:approvalrule_{name}', kwargs=kwargs or None)

    def test_staff_cannot_add_rule(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self._url('add')).status_code, 403)
        response = self.client.post(self._url('add'), {
            'priority': 1, 'action': 'any', 'outcome': RuleOutcomeChoices.SKIP, 'enabled': True,
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ApprovalRule.objects.filter(is_default=False).count(), 1)

    def test_staff_cannot_edit_or_delete_rule(self):
        self.client.force_login(self.staff)
        response = self.client.post(self._url('edit', pk=self.rule.pk), {
            'priority': 10, 'action': 'any', 'outcome': RuleOutcomeChoices.SKIP, 'enabled': True,
        })
        self.assertEqual(response.status_code, 403)
        response = self.client.post(self._url('delete', pk=self.rule.pk), {'confirm': True})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(self._url('bulk_delete'), {'pk': [self.rule.pk], 'confirm': True, '_confirm': True})
        self.assertEqual(response.status_code, 403)
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.outcome, RuleOutcomeChoices.REQUIRE)

    def test_staff_can_view_but_sees_no_change_buttons(self):
        self.client.force_login(self.staff)
        response = self.client.get(self._url('list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self._url('add'))
        self.assertNotContains(response, self._url('edit', pk=self.rule.pk))
        response = self.client.get(self.rule.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self._url('edit', pk=self.rule.pk))

    def test_superuser_can_change_rules(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self._url('add')).status_code, 200)
        response = self.client.get(self._url('list'))
        self.assertContains(response, self._url('edit', pk=self.rule.pk))
