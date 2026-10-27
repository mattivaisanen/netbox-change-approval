"""
Keep the changes the previous revision proposed, so a resubmitted request
can show what changed since it was sent back.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0021_keep_usernames'),
    ]

    operations = [
        migrations.AddField(
            model_name='changerequest',
            name='previous_diff',
            field=models.JSONField(
                blank=True, editable=False, null=True,
                help_text='The changes as the previous revision proposed them, before it was sent back.',
            ),
        ),
    ]
