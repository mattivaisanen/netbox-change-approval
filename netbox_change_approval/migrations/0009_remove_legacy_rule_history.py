from django.db import migrations

LEGACY_MODELS = ('approvalroutingrule', 'autoapprovalrule', 'exemptmodel')


def remove_legacy_rule_history(apps, schema_editor):
    """
    0008 dropped the three old rule tables, but NetBox still holds change log
    entries (and possibly bookmarks, notifications or subscriptions) for their
    object types. Those reference the types with PROTECT, so Django's
    remove_stale_contenttypes (run on every netbox-docker start) fails while
    they exist. The objects they describe are gone, so remove them; the types
    themselves are then cleaned up as usual by remove_stale_contenttypes.
    """
    ContentType = apps.get_model('contenttypes', 'ContentType')
    db = schema_editor.connection.alias
    stale = list(
        ContentType.objects.using(db)
        .filter(app_label='netbox_change_approval', model__in=LEGACY_MODELS)
        .values_list('pk', flat=True)
    )
    if not stale:
        return

    ObjectChange = apps.get_model('core', 'ObjectChange')
    ObjectChange.objects.using(db).filter(changed_object_type_id__in=stale).delete()
    ObjectChange.objects.using(db).filter(related_object_type_id__in=stale).update(related_object_type=None)
    for model_name in ('Bookmark', 'Notification', 'Subscription'):
        apps.get_model('extras', model_name).objects.using(db).filter(object_type_id__in=stale).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_approval', '0008_remove_legacy_rule_tables'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0011_move_objectchange'),
        ('extras', '0119_notifications'),
    ]

    operations = [
        migrations.RunPython(remove_legacy_rule_history, migrations.RunPython.noop),
    ]
