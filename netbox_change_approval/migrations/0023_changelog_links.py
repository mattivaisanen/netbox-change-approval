"""
Remember which changelog entries of the changed object a change request
labelled, so the request's own Changelog tab can list them.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0022_previous_diff'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChangeRequestLogEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('object_change_id', models.BigIntegerField()),
                ('change_request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='log_entries',
                    to='netbox_change_approval.changerequest',
                )),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('change_request', 'object_change_id'), name='nbca_one_link_per_changelog_entry',
                    ),
                ],
            },
        ),
    ]
