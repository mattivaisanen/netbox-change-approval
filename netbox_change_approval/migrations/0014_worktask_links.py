from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0013_pluginsetting'),
    ]

    operations = [
        migrations.AddField(
            model_name='worktask',
            name='links',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
