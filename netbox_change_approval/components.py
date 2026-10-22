"""
Components of a pending new object belong to its request.

While a new device (or VM) waits for approval, its interfaces, ports, bays,
inventory items, VM disks, and IP/MAC addresses assigned to its interfaces
are part of that same request instead of getting requests of their own:

- The person who submitted the device can add, edit and delete them freely.
  Each such change counts as an update of the device's request: its revision
  goes up (once per page submit, not per object), approvals so far start
  over and the approvers are notified again. The request page lists them.
- Everyone else is blocked from changing them, the same as the device.
- Approving the device approves them with it; rejecting it removes them
  with it (they are deleted along with the device).

Cables are not included: a cable joins two devices and keeps its own request.

Set PLUGINS_CONFIG['netbox_change_approval']['group_components_with_parent']
to False to turn this off (each component change is its own request again).
"""
from django.core.exceptions import FieldDoesNotExist

from .models import ActionChoices, StatusChoices

#: Foreign keys that make an object a component of its parent.
PARENT_FIELDS = ('device', 'virtual_machine')

#: Generic relations that attach an object to a component (an IP address or
#: MAC address assigned to an interface).
ASSIGNED_FIELDS = ('assigned_object',)

#: Request attribute: pks of parent requests already updated in this request.
_BUMPED_ATTR = '_nbca_parent_requests_updated'


def enabled():
    from netbox.plugins.utils import get_plugin_config

    return get_plugin_config('netbox_change_approval', 'group_components_with_parent') is not False


def _parents(instance):
    """The device/VM objects this instance is a component of (directly, or
    through the interface it is assigned to)."""
    found = []
    for name in PARENT_FIELDS:
        try:
            field = instance._meta.get_field(name)
        except FieldDoesNotExist:
            continue
        if not field.is_relation or not field.many_to_one:
            continue
        parent_id = getattr(instance, field.attname, None)
        if parent_id is not None:
            found.append((field.related_model, parent_id))
    for name in ASSIGNED_FIELDS:
        target = getattr(instance, name, None) if hasattr(instance, name) else None
        if target is not None and target is not instance:
            found.extend(_parents(target))
    return found


def _stored(instance):
    if instance.pk is None:
        return None
    return type(instance).objects.filter(pk=instance.pk).first()


def pending_parent_request(instance):
    """
    The pending *create* request of the new device/VM this instance belongs
    to (as it is now, or as it is being changed to), or None.
    """
    from core.models import ObjectType

    from .models import ChangeRequest

    if not enabled():
        return None
    parents = _parents(instance)
    stored = _stored(instance)
    if stored is not None:
        parents += _parents(stored)
    for model, pk in parents:
        change_request = ChangeRequest.objects.filter(
            content_type=ObjectType.objects.get_for_model(model), object_id=pk,
            status=StatusChoices.PENDING, action=ActionChoices.CREATE,
        ).first()
        if change_request is not None:
            return change_request
    return None


def gate_component(instance, action, user, request):
    """
    Called by gating._gate(). Returns (handled, result): when `handled`,
    _gate() returns `result` (None lets the write through); otherwise normal
    gating continues. Raises ObjectLocked for anyone but the submitter.
    """
    from .exceptions import ObjectLocked

    parent_request = pending_parent_request(instance)
    if parent_request is None:
        return False, None
    if user is None or parent_request.submitted_by_id != user.pk:
        raise ObjectLocked(parent_request, message=(
            f'This belongs to {parent_request.object_repr}, which is new and waiting for approval '
            f'(request #{parent_request.pk}). Until it is decided, only its submitter can change it.'
        ))
    _note_change(parent_request, request)
    return True, None


def _note_change(parent_request, request):
    """Count a component change as an update of the parent's request: once
    per page submit."""
    from . import notifications

    bumped = getattr(request, _BUMPED_ATTR, None) if request is not None else None
    if bumped is None:
        bumped = set()
        if request is not None:
            try:
                setattr(request, _BUMPED_ATTR, bumped)
            except AttributeError:
                pass
    if parent_request.pk in bumped:
        return
    bumped.add(parent_request.pk)
    parent_request.revision += 1
    parent_request.save(update_fields=['revision', 'last_updated'])
    notifications.request_updated(parent_request)


def component_summary(change_request):
    """
    For a pending new device/VM: [(label, count, list url)] of its
    components, shown on the request page. Empty for anything else.
    """
    from django.urls import NoReverseMatch

    from utilities.views import get_action_url

    from .discovery import is_gated_model

    if change_request.action != ActionChoices.CREATE or change_request.object_id is None:
        return []
    model = change_request.content_type.model_class()
    if model is None:
        return []
    rows = []
    for relation in model._meta.related_objects:
        if not relation.one_to_many or relation.field.name not in PARENT_FIELDS:
            continue
        related = relation.related_model
        if not is_gated_model(related):
            continue
        count = related.objects.filter(**{relation.field.name: change_request.object_id}).count()
        if not count:
            continue
        try:
            url = f'{get_action_url(related, action="list")}?{relation.field.name}_id={change_request.object_id}'
        except (NoReverseMatch, Exception):
            url = None
        rows.append((str(related._meta.verbose_name_plural).capitalize(), count, url))
    return sorted(rows)


def pending_parent_map(model, parent_field):
    """{parent pk: pending create request} for the parents of `model` objects
    via `parent_field` (for marking component rows in lists)."""
    from core.models import ObjectType

    from .models import ChangeRequest

    parent_model = model._meta.get_field(parent_field).related_model
    return {
        cr.object_id: cr for cr in ChangeRequest.objects.filter(
            content_type=ObjectType.objects.get_for_model(parent_model), status=StatusChoices.PENDING,
            action=ActionChoices.CREATE, object_id__isnull=False,
        ).only('pk', 'object_id', 'action', 'content_type')
    }
