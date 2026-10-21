import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('netbox_change_approval', '0006_changerequest_send_back'),
    ]

    operations = [
        migrations.AlterField(
            model_name='changerequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'), ('applied', 'Applied'), ('rejected', 'Rejected'),
                    ('retracted', 'Retracted'), ('returned', 'Sent back'), ('expired', 'Expired'),
                ],
                default='pending',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='approvalrule',
            name='required_approvals',
            field=models.PositiveSmallIntegerField(
                default=1,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(10),
                ],
                verbose_name='approvals needed',
            ),
        ),
        migrations.AddField(
            model_name='approvalrule',
            name='expire_after_days',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='expire after (days)'),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='required_approvals',
            field=models.PositiveSmallIntegerField(default=1, verbose_name='approvals needed'),
        ),
        migrations.AddField(
            model_name='changerequest',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='ChangeRequestApproval',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('revision', models.PositiveSmallIntegerField(default=1)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('change_request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='approvals',
                    to='netbox_change_approval.changerequest',
                )),
                ('user', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'approval',
                'verbose_name_plural': 'approvals',
                'ordering': ['created', 'pk'],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('change_request', 'user', 'revision'),
                        name='nbca_one_approval_per_user_and_revision',
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name='ChangeRequestComment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('text', models.TextField(max_length=2000)),
                ('change_request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='comments',
                    to='netbox_change_approval.changerequest',
                )),
                ('author', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'comment',
                'verbose_name_plural': 'comments',
                'ordering': ['created', 'pk'],
            },
        ),
    ]
