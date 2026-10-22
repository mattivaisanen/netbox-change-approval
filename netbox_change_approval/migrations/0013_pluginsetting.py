import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('netbox_change_approval', '0012_changeset'),
    ]

    operations = [
        migrations.CreateModel(
            name='PluginSetting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('key', models.CharField(max_length=100, unique=True)),
                ('value', models.JSONField(blank=True, null=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'plugin setting',
                'verbose_name_plural': 'plugin settings',
                'ordering': ['key'],
            },
        ),
    ]
