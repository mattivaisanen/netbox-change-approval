import django.core.serializers.json
import django.db.models.deletion
from django.db import migrations, models

# Hand-authored, same caveat as 0001/0002: verify with
#   python manage.py makemigrations netbox_change_approval --check --dry-run


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('netbox_change_approval', '0002_exemptmodel'),
    ]

    operations = [
        # Several exemptions per object type are now allowed (each with its own
        # constraint), so this is a plain ForeignKey instead of a OneToOneField.
        migrations.AlterField(
            model_name='exemptmodel',
            name='content_type',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to='core.objecttype',
                verbose_name='object type',
            ),
        ),
        migrations.AddField(
            model_name='exemptmodel',
            name='constraint',
            field=models.JSONField(
                blank=True,
                null=True,
                encoder=django.core.serializers.json.DjangoJSONEncoder,
                help_text=(
                    'Only exempt objects matching this filter (same format as rule '
                    'constraints), e.g. {"site__region__slug": "tampere"}. Leave blank to '
                    'exempt every object of this type.'
                ),
            ),
        ),
        migrations.AlterModelOptions(
            name='exemptmodel',
            options={
                'ordering': ['content_type', 'pk'],
                'verbose_name': 'exempt model',
                'verbose_name_plural': 'exempt models',
            },
        ),
    ]
