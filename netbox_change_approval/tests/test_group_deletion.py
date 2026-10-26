from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import ProtectedError
from django.test import TestCase

from core.models import ObjectType
from dcim.models import Site
from users.models import Group, User

from netbox_change_approval.api.serializers import ChangeRequestSerializer
from netbox_change_approval.models import ApprovalRule, ChangeRequest
from netbox_change_approval.repair import repair_dangling_links
from netbox_change_approval.tables import ChangeRequestTable


class GroupDeletionTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='Old approvers')
        cls.user = User.objects.create_user(username='alice')
        cls.site = Site.objects.create(name='Group S1', slug='group-s1')
        cls.site_type = ContentType.objects.get_for_model(Site)

    def _request(self, status):
        return ChangeRequest.objects.create(
            content_type=self.site_type, object_id=self.site.pk, action='update', status=status,
            object_repr='Group S1', submitted_by=self.user, approver_group=self.group,
        )

    def test_group_of_decided_requests_can_be_deleted_and_name_stays(self):
        request = self._request('applied')
        self.assertEqual(request.approver_group_name, 'Old approvers')

        self.group.delete()

        request.refresh_from_db()
        self.assertIsNone(request.approver_group)
        self.assertEqual(request.approver_group_label, 'Old approvers')
        self.assertEqual(ChangeRequestSerializer().get_approver_group(request), 'Old approvers')
        self.assertEqual(ChangeRequestTable([]).render_approver_group(None, request), 'Old approvers')

    def test_group_of_open_requests_cannot_be_deleted(self):
        for status in ('pending', 'returned'):
            with self.subTest(status=status):
                request = self._request(status)
                with self.assertRaises(ProtectedError), transaction.atomic():
                    self.group.delete()
                self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())
                request.delete()

    def test_group_used_by_a_rule_cannot_be_deleted(self):
        ApprovalRule.objects.create(
            priority=5, content_type=ObjectType.objects.get_for_model(Site), approver_group=self.group,
        )
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.group.delete()


class RepairDanglingLinksTest(TestCase):
    """Rows NetBox deleted while the plugin was removed (tables kept)."""

    def test_clears_or_deletes_links_to_rows_that_are_gone(self):
        user = User.objects.create_user(username='bob')
        group = Group.objects.create(name='Gone approvers')
        site_type = ContentType.objects.get_for_model(Site)
        request = ChangeRequest.objects.create(
            content_type=site_type, object_id=1, action='update', status='applied', object_repr='x',
            submitted_by=user, approver_group=group,
        )
        rule = ApprovalRule.objects.create(priority=7, content_type=ObjectType.objects.get_for_model(Site))
        missing = 10 ** 9
        ChangeRequest.objects.filter(pk=request.pk).update(submitted_by_id=missing, approver_group_id=missing)
        ApprovalRule.objects.filter(pk=rule.pk).update(content_type_id=missing)

        repair_dangling_links()

        request.refresh_from_db()
        self.assertIsNone(request.submitted_by_id)
        self.assertIsNone(request.approver_group_id)
        self.assertEqual(request.approver_group_label, 'Gone approvers')
        self.assertFalse(ApprovalRule.objects.filter(pk=rule.pk).exists())
