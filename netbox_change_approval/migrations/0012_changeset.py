import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('netbox_change_approval', '0011_worktask'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChangeSet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('ticket', models.CharField(blank=True, max_length=100)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('status', models.CharField(
                    choices=[('draft', 'Draft'), ('submitted', 'Submitted')], default='draft', max_length=10,
                )),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('batch_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'change set',
                'verbose_name_plural': 'change sets',
                'ordering': ['-created'],
            },
        ),
    ]
