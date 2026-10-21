from django.db import migrations

# The plugin used to tag pending creates with a "Pending Approval" tag. Pending
# objects are now shown by the yellow outline in lists instead, so the tag is
# removed from every object and deleted. Reversing this migration (rollback)
# re-creates the tag on objects that still have a pending create request.

TAG_SLUG = 'pending-approval'


def remove_tag(apps, schema_editor):
    Tag = apps.get_model('extras', 'Tag')
    TaggedItem = apps.get_model('extras', 'TaggedItem')
    tag = Tag.objects.filter(slug=TAG_SLUG).first()
    if tag is not None:
        TaggedItem.objects.filter(tag=tag).delete()
        tag.delete()


def restore_tag(apps, schema_editor):
    Tag = apps.get_model('extras', 'Tag')
    TaggedItem = apps.get_model('extras', 'TaggedItem')
    ChangeRequest = apps.get_model('netbox_change_approval', 'ChangeRequest')
    tag, _ = Tag.objects.get_or_create(
        slug=TAG_SLUG, defaults={'name': 'Pending Approval', 'color': 'ff9800'},
    )
    for cr in ChangeRequest.objects.filter(status='pending', action='create', object_id__isnull=False):
        TaggedItem.objects.get_or_create(
            tag=tag, content_type_id=cr.content_type_id, object_id=cr.object_id,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0001_initial'),
        ('netbox_change_approval', '0003_exemptmodel_constraint'),
    ]

    operations = [
        migrations.RunPython(remove_tag, restore_tag),
    ]
