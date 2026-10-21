import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('netbox_change_approval', '0005_approvalrule'),
    ]

    operations = [
        migrations.AlterField(
            model_name='changerequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'), ('applied', 'Applied'), ('rejected', 'Rejected'),
                    ('retracted', 'Retracted'), ('returned', 'Sent back'),
                ],
                default='pending',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='revision',
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text='Goes up by one each time the submitter resubmits after a send-back.',
            ),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='sent_back_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='sent_back_comment',
            field=models.TextField(
                blank=True,
                help_text='What the approver asked to change the last time this was sent back.',
            ),
        ),
        # Default True, so rejections from before this migration don't all
        # land in the new inbox at once.
        migrations.AddField(
            model_name='changerequest',
            name='seen_by_submitter',
            field=models.BooleanField(default=True),
        ),
    ]
