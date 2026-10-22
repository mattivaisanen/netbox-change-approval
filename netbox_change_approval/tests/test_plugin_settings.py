from django.test import TestCase, override_settings
from django.urls import reverse

from netbox.context import current_request
from users.models import Group, ObjectPermission, User

from .. import approvals, changesets, components, pending_marks, plugin_settings, worksheet
from ..models import PluginSetting
from ..permissions import _IS_APPROVER_SYNC_PERM_NAME
from .utils import fake_request

PLUGINS_CONFIG = {
    'netbox_change_approval': {
        'default_approver_group': 'Change Approvers',
        'exempt_models': ['extras.tag'],
        'expire_pending_after_days': 14,
        'pending_row_style': 'by_action',
        'group_components_with_parent': True,
        'work_sheets': True,
        'change_sets': True,
    },
}

URL = 'plugins:netbox_change_approval:settings'


@override_settings(PLUGINS_CONFIG=PLUGINS_CONFIG)
class SettingLookupTests(TestCase):
    def test_configuration_py_value_without_saved_value(self):
        self.assertEqual(plugin_settings.get_setting('expire_pending_after_days'), 14)
        self.assertEqual(plugin_settings.source('expire_pending_after_days'), 'config')
        self.assertEqual(plugin_settings.source('pending_row_style'), 'default')

    def test_saved_value_overrides_until_reset(self):
        self.assertTrue(plugin_settings.save('pending_row_style', 'outline'))
        self.assertEqual(pending_marks.style(), pending_marks.STYLE_OUTLINE)
        self.assertEqual(plugin_settings.source('pending_row_style'), 'saved')

        self.assertTrue(plugin_settings.reset('pending_row_style'))
        self.assertEqual(pending_marks.style(), pending_marks.STYLE_BY_ACTION)
        self.assertFalse(PluginSetting.objects.exists())

    def test_every_editable_setting_is_read_at_runtime(self):
        plugin_settings.save('expire_pending_after_days', 3)
        plugin_settings.save('group_components_with_parent', False)
        plugin_settings.save('work_sheets', False)
        plugin_settings.save('change_sets', False)
        self.assertFalse(changesets.enabled())
        self.assertEqual(approvals.default_expiry_days(), 3)
        self.assertFalse(components.enabled())
        self.assertFalse(worksheet.enabled())

    def test_saving_the_configuration_value_keeps_following_configuration(self):
        plugin_settings.save('expire_pending_after_days', 3)
        self.assertFalse(plugin_settings.save('expire_pending_after_days', 14))
        self.assertFalse(PluginSetting.objects.exists())

    def test_never_expire_means_the_same_as_zero(self):
        with override_settings(PLUGINS_CONFIG={'netbox_change_approval': {'expire_pending_after_days': None}}):
            self.assertFalse(plugin_settings.save('expire_pending_after_days', 0))
            self.assertFalse(PluginSetting.objects.exists())

    def test_exempt_models_only_from_configuration(self):
        PluginSetting.objects.create(key='exempt_models', value=[])
        self.assertEqual(plugin_settings.get_setting('exempt_models'), ['extras.tag'])

    def test_read_once_per_request_and_refreshed_after_save(self):
        token = current_request.set(fake_request(User.objects.create(username='u')))
        try:
            with self.assertNumQueries(1):
                plugin_settings.get_setting('pending_row_style')
                plugin_settings.get_setting('work_sheets')
            plugin_settings.save('pending_row_style', 'outline')
            self.assertEqual(plugin_settings.get_setting('pending_row_style'), 'outline')
        finally:
            current_request.reset(token)

    def test_changing_default_group_moves_the_approvals_menu(self):
        Group.objects.create(name='Change Approvers')
        board = Group.objects.create(name='Network Board')
        plugin_settings.save('default_approver_group', 'Network Board')
        groups = set(ObjectPermission.objects.get(name=_IS_APPROVER_SYNC_PERM_NAME).groups.all())
        self.assertEqual(groups, {board})


@override_settings(PLUGINS_CONFIG=PLUGINS_CONFIG)
class SettingsPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(username='admin', is_superuser=True)
        cls.alice = User.objects.create(username='alice')
        cls.approvers = Group.objects.create(name='Change Approvers')

    def _form(self, **changes):
        data = {
            'default_approver_group': 'Change Approvers',
            'expire_pending_after_days': '14',
            'pending_row_style': 'by_action',
            'group_components_with_parent': 'on',
            'work_sheets': 'on',
            'change_sets': 'on',
        }
        data.update(changes)
        return {key: value for key, value in data.items() if value is not None}

    def test_only_superusers(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse(URL)).status_code, 403)
        self.assertEqual(self.client.post(reverse(URL), self._form(work_sheets=None)).status_code, 403)
        self.assertFalse(PluginSetting.objects.exists())

    def test_page_shows_values_and_sources(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse(URL))
        self.assertContains(response, 'From configuration.py')
        self.assertContains(response, 'extras.tag')

    def test_save_overrides_and_reset_returns_to_configuration(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse(URL), self._form(pending_row_style='outline', work_sheets=None))
        self.assertRedirects(response, reverse(URL))
        self.assertEqual(
            dict(PluginSetting.objects.values_list('key', 'value')),
            {'pending_row_style': 'outline', 'work_sheets': False},
        )
        self.assertEqual(PluginSetting.objects.get(key='work_sheets').updated_by, self.admin)
        self.assertContains(self.client.get(reverse(URL)), 'Saved here')

        self.client.post(reverse(URL), {**self._form(), 'reset': 'work_sheets'})
        self.assertEqual(list(PluginSetting.objects.values_list('key', flat=True)), ['pending_row_style'])
        self.assertTrue(worksheet.enabled())

    def test_invalid_value_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse(URL), self._form(expire_pending_after_days='-1'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PluginSetting.objects.exists())

    def test_exempt_models_cannot_be_posted(self):
        self.client.force_login(self.admin)
        self.client.post(reverse(URL), self._form(exempt_models='dcim.site'))
        self.assertFalse(PluginSetting.objects.filter(key='exempt_models').exists())
