import django.core.serializers.json
import django.db.models.deletion
import taggit.managers
from django.conf import settings
from django.db import migrations, models

import utilities.json

# NOTE: hand-authored (no live NetBox instance was available while writing this
# plugin). Before relying on it, run:
#   python manage.py makemigrations netbox_change_approval --check --dry-run
# against a real NetBox install to confirm it matches models.py exactly, and
# regenerate if it reports drift. See DEVELOPER.md "Migrations" for details on
# exactly what to check and why this note exists.


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0001_initial'),
        ('extras', '0001_initial'),
        ('users', '0001_squashed_0011'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApprovalRoutingRule',
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
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
                ('content_type', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    to='core.objecttype',
                )),
                ('approver_group', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name='+', to='users.group',
                )),
            ],
            options={
                'verbose_name': 'approval routing rule',
                'verbose_name_plural': 'approval routing rules',
                'ordering': ['priority', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='AutoApprovalRule',
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
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
                ('content_type', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    to='core.objecttype',
                )),
            ],
            options={
                'verbose_name': 'auto-approval rule',
                'verbose_name_plural': 'auto-approval rules',
                'ordering': ['priority', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='ChangeRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('custom_field_data', models.JSONField(
                    blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder,
                )),
                ('object_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('object_repr', models.CharField(max_length=300)),
                ('action', models.CharField(
                    choices=[('create', 'Create'), ('update', 'Update'), ('delete', 'Delete')], max_length=10,
                )),
                ('diff', models.JSONField(
                    blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder,
                )),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('applied', 'Applied'), ('rejected', 'Rejected')],
                    default='pending', max_length=10,
                )),
                ('auto_approved', models.BooleanField(default=False)),
                ('batch_id', models.UUIDField(blank=True, db_index=True, null=True)),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_comment', models.TextField(blank=True)),
                ('applied_at', models.DateTimeField(blank=True, null=True)),
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
                ('content_type', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='+', to='contenttypes.contenttype',
                )),
                ('approver_group', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='users.group',
                )),
                ('submitted_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('decided_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'change request',
                'verbose_name_plural': 'change requests',
                'ordering': ['-submitted_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='changerequest',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status', 'pending')),
                fields=('content_type', 'object_id'),
                name='nbca_unique_pending_change_per_object',
            ),
        ),
    ]
