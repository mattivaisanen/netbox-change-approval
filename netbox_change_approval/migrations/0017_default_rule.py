"""
The Default rule: one ApprovalRule row (is_default) at the bottom of the rule
list that decides every change no other rule matches.

It takes over two settings from the settings page: its approver group and
expiry start as the values in effect when this migration runs (saved on the
settings page, else configuration.py), and those saved settings are removed.
"""
from django.conf import settings
from django.db import migrations, models

APP = 'netbox_change_approval'
DEFAULT_PRIORITY = 2147483647


def _value_in_effect(PluginSetting, key, fallback):
    row = PluginSetting.objects.filter(key=key).first()
    if row is not None:
        return row.value
    return (getattr(settings, 'PLUGINS_CONFIG', {}).get(APP) or {}).get(key, fallback)


def create_default_rule(apps, schema_editor):
    ApprovalRule = apps.get_model(APP, 'ApprovalRule')
    PluginSetting = apps.get_model(APP, 'PluginSetting')
    Group = apps.get_model('users', 'Group')

    if not ApprovalRule.objects.filter(is_default=True).exists():
        group_name = _value_in_effect(PluginSetting, 'default_approver_group', 'Change Approvers')
        days = _value_in_effect(PluginSetting, 'expire_pending_after_days', None)
        try:
            days = int(days) if days else None
        except (TypeError, ValueError):
            days = None
        ApprovalRule.objects.create(
            is_default=True,
            priority=DEFAULT_PRIORITY,
            outcome='require',
            action='any',
            enabled=True,
            description='Default rule',
            approver_group=Group.objects.filter(name=group_name).first() if group_name else None,
            expire_after_days=days,
        )
    PluginSetting.objects.filter(key__in=('default_approver_group', 'expire_pending_after_days')).delete()


def remove_default_rule(apps, schema_editor):
    apps.get_model(APP, 'ApprovalRule').objects.filter(is_default=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0016_changerequest_schedule'),
    ]

    operations = [
        migrations.AddField(
            model_name='approvalrule',
            name='is_default',
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AlterModelOptions(
            name='approvalrule',
            options={
                'ordering': ['is_default', 'priority', 'pk'],
                'verbose_name': 'approval rule',
                'verbose_name_plural': 'approval rules',
            },
        ),
        migrations.AddConstraint(
            model_name='approvalrule',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_default=True), fields=('is_default',),
                name='netbox_change_approval_one_default_rule',
            ),
        ),
        migrations.RunPython(create_default_rule, remove_default_rule),
    ]
