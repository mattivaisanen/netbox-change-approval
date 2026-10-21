import django.core.serializers.json
import django.db.models.deletion
import taggit.managers
from django.db import migrations, models

import utilities.json

# Hand-authored, same caveat as the earlier migrations: verify with
#   python manage.py makemigrations netbox_change_approval --check --dry-run
#
# Adds the single ApprovalRule table and copies every Exempt Models, Auto-
# Approval Rules and Approval Routing Rules row into it, keeping the order in
# which the old code checked them: exemptions first, then auto-approval rules,
# then routing rules (each group in its own priority order), renumbered
# 10, 20, 30, ... The old tables are left untouched, so rolling back
# (migrate netbox_change_approval 0004) just drops the new table.


def copy_rules(apps, schema_editor):
    ApprovalRule = apps.get_model('netbox_change_approval', 'ApprovalRule')
    ExemptModel = apps.get_model('netbox_change_approval', 'ExemptModel')
    AutoApprovalRule = apps.get_model('netbox_change_approval', 'AutoApprovalRule')
    ApprovalRoutingRule = apps.get_model('netbox_change_approval', 'ApprovalRoutingRule')

    priority = 0

    def add(**fields):
        nonlocal priority
        priority += 10
        ApprovalRule.objects.create(priority=priority, **fields)

    for row in ExemptModel.objects.order_by('content_type_id', 'pk'):
        add(
            content_type_id=row.content_type_id, constraint=row.constraint, enabled=row.enabled,
            description=row.description, action='any', outcome='skip',
        )
    for row in AutoApprovalRule.objects.order_by('priority', 'pk'):
        add(
            content_type_id=row.content_type_id, constraint=row.constraint, enabled=row.enabled,
            description=row.description, action=row.action, outcome='auto',
        )
    for row in ApprovalRoutingRule.objects.order_by('priority', 'pk'):
        add(
            content_type_id=row.content_type_id, constraint=row.constraint, enabled=row.enabled,
            description=row.description, action='any', outcome='require',
            approver_group_id=row.approver_group_id,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('extras', '0001_initial'),
        ('users', '0001_squashed_0011'),
        ('netbox_change_approval', '0004_remove_pending_approval_tag'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApprovalRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('custom_field_data', models.JSONField(
                    blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder,
                )),
                ('priority', models.PositiveIntegerField(default=100)),
                ('constraint', models.JSONField(
                    blank=True, null=True, encoder=django.core.serializers.json.DjangoJSONEncoder,
                )),
                ('enabled', models.BooleanField(default=True)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('action', models.CharField(
                    choices=[('create', 'Create'), ('update', 'Update'), ('delete', 'Delete'), ('any', 'Any')],
                    default='any', max_length=10,
                )),
                ('outcome', models.CharField(
                    choices=[
                        ('require', 'Needs approval'), ('auto', 'Approve automatically'),
                        ('skip', 'Skip approval'),
                    ],
                    default='require', max_length=10,
                )),
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
                ('content_type', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    to='core.objecttype',
                )),
                ('approver_group', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='+', to='users.group',
                    help_text='Only for "Needs approval". Leave blank to use the default approver group.',
                )),
            ],
            options={
                'verbose_name': 'approval rule',
                'verbose_name_plural': 'approval rules',
                'ordering': ['priority', 'pk'],
            },
        ),
        migrations.RunPython(copy_rules, migrations.RunPython.noop),
    ]
