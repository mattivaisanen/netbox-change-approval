from django.db import migrations


class Migration(migrations.Migration):
    """Drop the three rule tables that Approval Rules replaced. Migration 0005
    already copied every row from them into ApprovalRule."""

    dependencies = [
        ('netbox_change_approval', '0007_approvals_comments_expiry'),
    ]

    operations = [
        migrations.DeleteModel(name='ApprovalRoutingRule'),
        migrations.DeleteModel(name='AutoApprovalRule'),
        migrations.DeleteModel(name='ExemptModel'),
    ]
