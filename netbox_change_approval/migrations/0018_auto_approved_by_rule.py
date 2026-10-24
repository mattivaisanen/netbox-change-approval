"""Remember which rule auto-approved a change request, so it can be shown as
the approver. Requests logged before this keep a blank rule."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0017_default_rule'),
    ]

    operations = [
        migrations.AddField(
            model_name='changerequest',
            name='auto_approved_by_rule',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='netbox_change_approval.approvalrule',
            ),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='auto_approved_by_rule_name',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
