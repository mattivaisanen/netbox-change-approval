from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0014_worktask_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='approvalrule',
            name='require_reason',
            field=models.BooleanField(default=False, verbose_name='reason required'),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='reason',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
