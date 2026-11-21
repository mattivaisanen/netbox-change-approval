from django.db.models.signals import pre_delete
from django.test import TransactionTestCase

from core.models import ObjectType
from dcim.models import Manufacturer, Site
from netbox.context import current_request
from users.models import User

from .. import signals
from ..context import delete_scope
from ..exceptions import ChangeApprovalRequired
from ..models import ApprovalRule, ChangeRequest, RuleOutcomeChoices
from .utils import fake_request, grant_bypass


class NestedDeleteTests(TransactionTestCase):
    """
    Another plugin's pre_delete receiver may delete related rows itself (for
    example a relationship plugin removing the links of a deleted object).
    Those nested deletes have their own `origin`, but they follow the delete
    that started them: once that one is allowed, they pass too.

    Here deleting a manufacturer also deletes a site from a receiver; the user
    may delete manufacturers without approval but not sites.
    """

    def setUp(self):
        self.alice = User.objects.create(username='alice')
        self.manufacturer = Manufacturer.objects.create(name='M', slug='m')
        self.site = Site.objects.create(name='S', slug='s')
        token = current_request.set(fake_request(self.alice))
        self.addCleanup(current_request.reset, token)

    def _delete_site_with_manufacturer(self, receiver_first=False):
        site = self.site

        def delete_related(sender, instance, **kwargs):
            Site.objects.get(pk=site.pk).delete()

        if receiver_first:
            # The other plugin's receiver was connected before this plugin's.
            uid = f'netbox_change_approval:{Manufacturer._meta.label}:delete'
            pre_delete.disconnect(sender=Manufacturer, dispatch_uid=uid)
            pre_delete.connect(delete_related, sender=Manufacturer, weak=False)
            pre_delete.connect(signals.gate_delete, sender=Manufacturer, dispatch_uid=uid)
        else:
            pre_delete.connect(delete_related, sender=Manufacturer, weak=False)
        self.addCleanup(pre_delete.disconnect, delete_related, sender=Manufacturer)

    def _assert_both_deleted(self):
        self.assertFalse(Manufacturer.objects.filter(pk=self.manufacturer.pk).exists())
        self.assertFalse(Site.objects.filter(pk=self.site.pk).exists())
        self.assertIsNone(delete_scope.get())

    def test_site_delete_alone_needs_approval(self):
        with self.assertRaises(ChangeApprovalRequired):
            Site.objects.get(pk=self.site.pk).delete()
        self.assertIsNone(delete_scope.get())

    def test_bypass_on_outer_delete_lets_nested_delete_through(self):
        grant_bypass(self.alice, Manufacturer)
        self._delete_site_with_manufacturer()

        self.manufacturer.delete()

        self._assert_both_deleted()

    def test_nested_delete_through_when_receiver_runs_before_gate(self):
        grant_bypass(self.alice, Manufacturer)
        self._delete_site_with_manufacturer(receiver_first=True)

        self.manufacturer.delete()

        self._assert_both_deleted()

    def test_auto_approved_outer_delete_lets_nested_delete_through(self):
        ApprovalRule.objects.create(
            priority=1, outcome=RuleOutcomeChoices.AUTO, action='delete',
            content_type=ObjectType.objects.get_for_model(Manufacturer),
        )
        self._delete_site_with_manufacturer()

        self.manufacturer.delete()

        self._assert_both_deleted()
        # One auto-approved request, for the manufacturer only.
        self.assertEqual(ChangeRequest.objects.count(), 1)

    def test_outer_delete_needing_approval_is_queued_for_the_outer_object(self):
        self._delete_site_with_manufacturer(receiver_first=True)

        with self.assertRaises(ChangeApprovalRequired) as cm:
            self.manufacturer.delete()

        self.assertEqual(cm.exception.change_request.content_type.model_class(), Manufacturer)
        self.assertTrue(Site.objects.filter(pk=self.site.pk).exists())
        self.assertIsNone(delete_scope.get())

    def test_scope_does_not_outlive_the_allowed_delete(self):
        grant_bypass(self.alice, Manufacturer)
        self.manufacturer.delete()

        with self.assertRaises(ChangeApprovalRequired):
            Site.objects.get(pk=self.site.pk).delete()
