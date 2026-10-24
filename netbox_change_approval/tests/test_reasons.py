from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import Group, ObjectPermission, User

from ..models import ApprovalRule, ChangeRequest, RuleOutcomeChoices, StatusChoices


class ReasonTests(TestCase):
    """The changelog message becomes the request's reason; a rule can
    require one."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.get_or_create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        perm = ObjectPermission.objects.create(name='m', actions=['view', 'add', 'change', 'delete'])
        perm.object_types.set([ObjectType.objects.get_for_model(Manufacturer)])
        perm.users.set([cls.alice])

    def setUp(self):
        self.vendor = Manufacturer.objects.create(name='Acme', slug='acme')
        self.client.force_login(self.alice)

    def _edit(self, message=''):
        return self.client.post(reverse('dcim:manufacturer_edit', kwargs={'pk': self.vendor.pk}), {
            'name': 'Acme', 'slug': 'acme', 'description': 'changed', 'changelog_message': message, '_update': '',
        })

    def test_changelog_message_becomes_reason(self):
        self._edit('CHG-42 new contract')
        change_request = ChangeRequest.objects.get(object_id=self.vendor.pk, status=StatusChoices.PENDING)
        self.assertEqual(change_request.reason, 'CHG-42 new contract')
        self.assertContains(self.client.get(change_request.get_absolute_url()), 'CHG-42 new contract')

    def test_rule_can_require_reason(self):
        ApprovalRule.objects.create(
            priority=1, content_type=ObjectType.objects.get_for_model(Manufacturer),
            outcome=RuleOutcomeChoices.REQUIRE, require_reason=True,
        )
        response = self._edit('')
        self.assertEqual(response.status_code, 200)  # form shown again with the error
        self.assertContains(response, 'asks for a reason')
        self.assertFalse(ChangeRequest.objects.exists())
        self._edit('CHG-43')
        self.assertEqual(ChangeRequest.objects.get().reason, 'CHG-43')

    def test_required_reason_on_delete(self):
        ApprovalRule.objects.create(
            priority=1, content_type=ObjectType.objects.get_for_model(Manufacturer),
            outcome=RuleOutcomeChoices.REQUIRE, require_reason=True,
        )
        self.client.post(reverse('dcim:manufacturer_delete', kwargs={'pk': self.vendor.pk}), {'confirm': True})
        self.assertFalse(ChangeRequest.objects.exists())
        self.assertTrue(Manufacturer.objects.filter(pk=self.vendor.pk).exists())
        self.client.post(reverse('dcim:manufacturer_delete', kwargs={'pk': self.vendor.pk}),
                         {'confirm': True, 'changelog_message': 'retired'})
        self.assertEqual(ChangeRequest.objects.get().reason, 'retired')
