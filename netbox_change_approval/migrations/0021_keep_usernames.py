"""
Keep the username next to each user link (who submitted, decided, sent back,
scheduled, approved, commented, did field work, made a change set or saved a
setting), so it still shows after the user is deleted.
"""
from django.db import migrations, models
from django.db.models.functions import Coalesce

FIELDS = {
    'changerequest': ('submitted_by', 'decided_by', 'sent_back_by', 'scheduled_by'),
    'changerequestapproval': ('user',),
    'changerequestcomment': ('author',),
    'worktask': ('done_by',),
    'changeset': ('created_by',),
    'pluginsetting': ('updated_by',),
}


def copy_usernames(apps, schema_editor):
    User = apps.get_model('users', 'User')
    for model_name, fields in FIELDS.items():
        model = apps.get_model('netbox_change_approval', model_name)
        for field in fields:
            username = User.objects.filter(pk=models.OuterRef(f'{field}_id')).values('username')[:1]
            model.objects.filter(**{f'{field}__isnull': False}).update(
                **{f'{field}_name': Coalesce(models.Subquery(username), models.Value(''))},
            )


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0020_release_netbox_rows'),
        ('users', '0001_squashed_0011'),
    ]

    operations = [
        migrations.AddField(
            model_name='changerequest',
            name='decided_by_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='scheduled_by_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='sent_back_by_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='submitted_by_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='changerequestapproval',
            name='user_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='changerequestcomment',
            name='author_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='changeset',
            name='created_by_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='pluginsetting',
            name='updated_by_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='worktask',
            name='done_by_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.RunPython(copy_usernames, migrations.RunPython.noop),
    ]
