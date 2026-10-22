import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('netbox_change_approval', '0009_remove_legacy_rule_history'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApproverAbsence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('starts', models.DateField(verbose_name='from')),
                ('ends', models.DateField(blank=True, null=True, verbose_name='until')),
                ('note', models.CharField(blank=True, max_length=200)),
                ('stand_in', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='+',
                    to=settings.AUTH_USER_MODEL, verbose_name='stand-in',
                )),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'approver absence',
                'verbose_name_plural': 'approver absences',
            },
        ),
    ]
