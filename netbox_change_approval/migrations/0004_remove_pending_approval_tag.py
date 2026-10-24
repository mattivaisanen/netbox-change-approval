from django.db import migrations

# The plugin used to tag pending creates with a "Pending Approval" tag. Pending
# objects are now shown by the yellow outline in lists instead, so the tag is
# removed from every object and deleted. Reversing it does nothing: bringing
# the old tag back (e.g. during `migrate netbox_change_approval zero` before an
# uninstall) would only leave a stray tag behind.

TAG_SLUG = 'pending-approval'


def remove_tag(apps, schema_editor):
    Tag = apps.get_model('extras', 'Tag')
    TaggedItem = apps.get_model('extras', 'TaggedItem')
    tag = Tag.objects.filter(slug=TAG_SLUG).first()
    if tag is not None:
        TaggedItem.objects.filter(tag=tag).delete()
        tag.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0001_initial'),
        ('netbox_change_approval', '0003_exemptmodel_constraint'),
    ]

    operations = [
        migrations.RunPython(remove_tag, migrations.RunPython.noop),
    ]
