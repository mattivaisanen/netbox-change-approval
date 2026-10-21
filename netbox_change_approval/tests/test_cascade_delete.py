from django.test import TransactionTestCase

from core.models import ObjectType
from dcim.models import Location, Site
from netbox.context import current_request
from users.models import User

from ..exceptions import ChangeApprovalRequired
from ..models import ActionChoices, ApprovalRule, ChangeRequest, RuleOutcomeChoices, StatusChoices
from .utils import fake_request, grant_bypass


class CascadeDeleteTests(TransactionTestCase):
    """
    Deleting an object also deletes its dependents, and Django signals the
    dependents first. The request must be about the object the user deleted,
    not about whichever dependent happened to be signalled first.

    TransactionTestCase for the same reason as GateDeleteSignalTests.
    """

    def setUp(self):
        self.alice = User.objects.create(username='alice')
        self.site = Site.objects.create(name='Site 1', slug='site-1')
        self.location = Location.objects.create(site=self.site, name='Room 1', slug='room-1')
        token = current_request.set(fake_request(self.alice))
        self.addCleanup(current_request.reset, token)

    def test_deleting_parent_queues_the_parent(self):
        with self.assertRaises(ChangeApprovalRequired) as cm:
            self.site.delete()

        change_request = cm.exception.change_request
        self.assertEqual(change_request.action, ActionChoices.DELETE)
        self.assertEqual(change_request.content_type.model_class(), Site)
        self.assertEqual(change_request.object_id, self.site.pk)
        self.assertTrue(Site.objects.filter(pk=self.site.pk).exists())
        self.assertTrue(Location.objects.filter(pk=self.location.pk).exists())

    def test_approving_parent_delete_removes_dependents(self):
        with self.assertRaises(ChangeApprovalRequired) as cm:
            self.site.delete()
        change_request = cm.exception.change_request
        change_request.save()  # what the middleware does

        change_request.apply()

        self.assertFalse(Site.objects.filter(pk=self.site.pk).exists())
        self.assertFalse(Location.objects.filter(pk=self.location.pk).exists())

    def test_bypass_on_parent_covers_dependents(self):
        grant_bypass(self.alice, Site)
        site_pk = self.site.pk

        self.site.delete()  # should not raise

        self.assertFalse(Site.objects.filter(pk=site_pk).exists())
        self.assertFalse(Location.objects.filter(pk=self.location.pk).exists())

    def test_bypass_on_dependent_only_does_not_delete_parent(self):
        grant_bypass(self.alice, Location)

        with self.assertRaises(ChangeApprovalRequired) as cm:
            self.site.delete()

        self.assertEqual(cm.exception.change_request.content_type.model_class(), Site)
        self.assertTrue(Site.objects.filter(pk=self.site.pk).exists())

    def test_auto_approved_parent_delete_logs_one_request(self):
        ApprovalRule.objects.create(
            priority=1, outcome=RuleOutcomeChoices.AUTO, action='delete',
            content_type=ObjectType.objects.get_for_model(Site),
        )
        site_pk = self.site.pk

        self.site.delete()

        self.assertFalse(Site.objects.filter(pk=site_pk).exists())
        logged = ChangeRequest.objects.filter(action=ActionChoices.DELETE, status=StatusChoices.APPLIED)
        self.assertEqual(logged.count(), 1)
        self.assertEqual(logged.get().object_id, site_pk)

    def test_deleting_dependent_alone_is_still_gated(self):
        with self.assertRaises(ChangeApprovalRequired) as cm:
            self.location.delete()
        self.assertEqual(cm.exception.change_request.content_type.model_class(), Location)
