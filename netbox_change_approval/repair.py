"""
Clear links to NetBox rows that were deleted while the plugin was removed.

The plugin's links to NetBox's own tables (users, groups, object types, sites,
racks) have no database constraint, so NetBox can delete those rows while the
plugin is uninstalled and its tables are kept. Installing it again then finds
links to rows that are gone. This runs after every `migrate` and handles each
link the way its on_delete would have: CASCADE deletes the row (e.g. a rule
for an object type that no longer exists), anything else clears the link.
"""
import logging

from django.apps import apps
from django.db import models, transaction

logger = logging.getLogger('netbox_change_approval')

APP_LABEL = 'netbox_change_approval'


def _outside_links():
    for model in apps.get_app_config(APP_LABEL).get_models():
        for field in model._meta.concrete_fields:
            if field.is_relation and field.many_to_one and field.related_model._meta.app_label != APP_LABEL:
                yield model, field


def repair_dangling_links():
    with transaction.atomic():
        for model, field in _outside_links():
            target = field.related_model._base_manager.values('pk')
            dangling = model._base_manager.exclude(**{f'{field.attname}__isnull': True}).exclude(
                **{f'{field.attname}__in': target},
            )
            if not dangling.exists():
                continue
            if field.remote_field.on_delete is models.CASCADE or not field.null:
                count = dangling.count()
                dangling.delete()
                action = 'deleted'
            else:
                count = dangling.update(**{field.attname: None})
                action = 'cleared'
            logger.info(
                '%s %d %s rows linking to %s rows deleted while the plugin was removed',
                action, count, model._meta.label, field.related_model._meta.label,
            )
