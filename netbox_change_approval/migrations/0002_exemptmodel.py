import django.db.models.deletion
import taggit.managers
from django.db import migrations, models

import utilities.json

# NOTE: hand-authored, same caveat as 0001_initial.py — run
#   python manage.py makemigrations netbox_change_approval --check --dry-run
# against a real NetBox install before relying on it. See DEVELOPER.md
# "Migrations" for details.


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('extras', '0001_initial'),
        ('netbox_change_approval', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExemptModel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('custom_field_data', models.JSONField(
                    blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder,
                )),
                ('enabled', models.BooleanField(default=True)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
                ('content_type', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, to='core.objecttype',
                )),
            ],
            options={
                'verbose_name': 'exempt model',
                'verbose_name_plural': 'exempt models',
                'ordering': ['content_type'],
            },
        ),
    ]
