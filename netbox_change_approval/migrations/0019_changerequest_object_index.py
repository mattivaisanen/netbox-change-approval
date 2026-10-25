"""Index change requests by object, for the Change Requests tab on object
pages."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0018_auto_approved_by_rule'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='changerequest',
            index=models.Index(fields=['content_type', 'object_id', 'status'], name='nbca_cr_object'),
        ),
    ]
