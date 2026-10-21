from django.test import TestCase

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import Group, ObjectPermission, User

from .. import gating, permissions
from ..models import ActionChoices, ChangeRequest
from .utils import fake_request


class VisibleChangeRequestsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Approvers')
        cls.other_group = Group.objects.create(name='Other Group')
        cls.submitter = User.objects.create(username='submitter')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.outsider = User.objects.create(username='outsider')
        cls.admin = User.objects.create(username='admin')

        # CREATE no longer aborts through gate()'s return value (see gating.gate()'s
        # docstring) — it stashes the ChangeRequest on the instance and lets save()
        # proceed, same as the real validator -> form.save() flow.
        unsaved = Manufacturer(name='Acme', slug='acme')
        result = gating.gate(unsaved, ActionChoices.CREATE, fake_request(cls.submitter))
        assert result is None
        unsaved.save()
        cls.change_request = ChangeRequest.objects.get(object_id=unsaved.pk)
        cls.change_request.approver_group = cls.approvers
        cls.change_request.save()

    def test_submitter_sees_own_request(self):
        visible = permissions.visible_change_requests(self.submitter, ChangeRequest.objects.all())
        self.assertIn(self.change_request, visible)

    def test_approver_group_member_sees_routed_request(self):
        visible = permissions.visible_change_requests(self.approver, ChangeRequest.objects.all())
        self.assertIn(self.change_request, visible)

    def test_user_without_view_permission_does_not_see_it(self):
        visible = permissions.visible_change_requests(self.outsider, ChangeRequest.objects.all())
        self.assertNotIn(self.change_request, visible)
        self.assertFalse(permissions.can_view_change_request(self.outsider, self.change_request))

    def test_user_who_can_view_the_object_type_sees_it(self):
        viewer = User.objects.create(username='viewer')
        perm = ObjectPermission.objects.create(name='view-manufacturers', enabled=True, actions=['view'])
        perm.object_types.set([ObjectType.objects.get_for_model(Manufacturer)])
        perm.users.set([viewer])

        visible = permissions.visible_change_requests(viewer, ChangeRequest.objects.all())
        self.assertIn(self.change_request, visible)
        self.assertTrue(permissions.can_view_change_request(viewer, self.change_request))

    def test_approver_queue_only_has_own_groups_requests(self):
        self.assertIn(self.change_request, permissions.approver_queue(self.approver, ChangeRequest.objects.all()))
        self.assertNotIn(self.change_request, permissions.approver_queue(self.outsider, ChangeRequest.objects.all()))

    def test_superuser_sees_everything_without_any_grant(self):
        root = User.objects.create(username='root', is_superuser=True)
        visible = permissions.visible_change_requests(root, ChangeRequest.objects.all())
        self.assertIn(self.change_request, visible)


def ct_for_changerequest():
    from ..models import ChangeRequest
    return ObjectType.objects.get_for_model(ChangeRequest)
