"""
Let NetBox delete users, groups, object types, sites and racks the plugin
links to, also once the plugin is removed with its tables kept: those links
lose their database constraint (Django still applies on_delete while the
plugin is installed). Decided change requests no longer keep their approver
group from being deleted; they keep its name instead.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_group_names(apps, schema_editor):
    ChangeRequest = apps.get_model('netbox_change_approval', 'ChangeRequest')
    Group = apps.get_model('users', 'Group')
    for group in Group.objects.filter(pk__in=ChangeRequest.objects.values('approver_group_id')):
        ChangeRequest.objects.filter(approver_group_id=group.pk).update(approver_group_name=group.name)


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0019_changerequest_object_index'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='changerequest',
            name='approver_group_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AlterField(
            model_name='approvalrule',
            name='approver_group',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='users.group'),
        ),
        migrations.AlterField(
            model_name='approvalrule',
            name='content_type',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.CASCADE, to='core.objecttype'),
        ),
        migrations.AlterField(
            model_name='approverabsence',
            name='stand_in',
            field=models.ForeignKey(db_constraint=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='approverabsence',
            name='user',
            field=models.OneToOneField(db_constraint=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='changerequest',
            name='approver_group',
            field=models.ForeignKey(db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='users.group'),
        ),
        migrations.AlterField(
            model_name='changerequest',
            name='content_type',
            field=models.ForeignKey(db_constraint=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='contenttypes.contenttype'),
        ),
        migrations.AlterField(
            model_name='changerequest',
            name='decided_by',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='changerequest',
            name='scheduled_by',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='changerequest',
            name='sent_back_by',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='changerequest',
            name='submitted_by',
            field=models.ForeignKey(db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='changerequestapproval',
            name='user',
            field=models.ForeignKey(db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='changerequestcomment',
            name='author',
            field=models.ForeignKey(db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='changeset',
            name='created_by',
            field=models.ForeignKey(db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='pluginsetting',
            name='updated_by',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='worktask',
            name='done_by',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='worktask',
            name='rack',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='dcim.rack'),
        ),
        migrations.AlterField(
            model_name='worktask',
            name='site',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='dcim.site'),
        ),
        migrations.RunPython(copy_group_names, migrations.RunPython.noop),
    ]
