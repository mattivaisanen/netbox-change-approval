import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('dcim', '0001_initial'),
        ('netbox_change_approval', '0010_approverabsence'),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('text', models.CharField(max_length=300)),
                ('done_at', models.DateTimeField(blank=True, null=True)),
                ('change_request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='work_tasks',
                    to='netbox_change_approval.changerequest',
                )),
                ('done_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('rack', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to='dcim.rack',
                )),
                ('site', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to='dcim.site',
                )),
            ],
            options={
                'verbose_name': 'work task',
                'verbose_name_plural': 'work tasks',
                'ordering': ['pk'],
            },
        ),
    ]
