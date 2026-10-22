from django.test import TestCase, override_settings

from users.models import Group, ObjectPermission

from ..permissions import _IS_APPROVER_SYNC_PERM_NAME, sync_is_approver_permission

PLUGINS_CONFIG = {
    'netbox_change_approval': {
        'default_approver_group': 'Late Approvers',
        'exempt_models': [],
    },
}


@override_settings(PLUGINS_CONFIG=PLUGINS_CONFIG)
class ApproverMenuPermissionTests(TestCase):
    """The "Approvals" menu permission follows the default approver group even
    when that group is created or renamed after NetBox started."""

    def _groups(self):
        return set(ObjectPermission.objects.get(name=_IS_APPROVER_SYNC_PERM_NAME).groups.all())

    def test_default_group_created_later_gets_the_menu(self):
        sync_is_approver_permission()
        self.assertEqual(self._groups(), set())

        group = Group.objects.create(name='Late Approvers')

        self.assertEqual(self._groups(), {group})

    def test_renaming_away_from_default_removes_the_menu(self):
        group = Group.objects.create(name='Late Approvers')
        self.assertEqual(self._groups(), {group})

        group.name = 'Former Approvers'
        group.save()

        self.assertEqual(self._groups(), set())
