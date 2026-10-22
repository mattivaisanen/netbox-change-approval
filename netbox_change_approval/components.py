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

A new cable with an end on such a pending device (say, from its port to an
existing patch panel) joins that device's request too, so racking and
patching a device is one decision. An existing cable re-pointed onto a pending
device still needs its own approval.

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
    from .plugin_settings import get_setting

    return get_setting('group_components_with_parent') is not False


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
    to, or None.

    Only when the object belongs to pending new objects both as it is stored
    now and as it is being changed to. An object of an approved device (or one
    with no device at all, like an unassigned IP) moved onto a pending device,
    or moved off one onto an approved device, goes through normal approval:
    otherwise its submitter could move approved objects without approval, and
    withdrawing the device would then delete them.
    """
    if not enabled():
        return None
    if _is_cable(instance):
        requests = cable_requests(instance)
        return requests[0] if requests else None
    states = [_parents(instance)]
    stored = _stored(instance)
    if stored is not None:
        states.append(_parents(stored))
    found = None
    for parents in states:
        if not parents:
            return None
        for model, pk in parents:
            change_request = _pending_create(model, pk)
            if change_request is None:
                return None
            found = found or change_request
    return found


def _is_cable(instance):
    return instance._meta.label_lower == 'dcim.cable'


def _cable_end_requests(ends):
    found = []
    for end in ends or ():
        for model, pk in _parents(end):
            change_request = _pending_create(model, pk)
            if change_request is not None and change_request not in found:
                found.append(change_request)
    return found


def cable_requests(cable):
    """
    Pending new-device requests a cable belongs to: those of the pending
    devices at its ends. A new cable belongs to them if any end is on one; an
    existing cable only if it was already on that device and still is (so an
    approved cable can't be moved onto a pending device without approval).
    """
    new_ends = list(getattr(cable, 'a_terminations', None) or []) + list(getattr(cable, 'b_terminations', None) or [])
    requests = _cable_end_requests(new_ends)
    stored = _stored(cable)
    if stored is not None:
        before = _cable_end_requests(list(stored.a_terminations) + list(stored.b_terminations))
        requests = [cr for cr in requests if cr in before]
    return requests


def _pending_create(model, pk):
    from core.models import ObjectType

    from .models import ChangeRequest

    return ChangeRequest.objects.filter(
        content_type=ObjectType.objects.get_for_model(model), object_id=pk,
        status=StatusChoices.PENDING, action=ActionChoices.CREATE,
    ).first()


def gate_component(instance, action, user, request):
    """
    Called by gating._gate(). Returns (handled, result): when `handled`,
    _gate() returns `result` (None lets the write through); otherwise normal
    gating continues. Raises ObjectLocked for anyone but the submitter.
    """
    if not enabled():
        return False, None
    if _is_cable(instance):
        parent_requests = cable_requests(instance)
    else:
        parent_request = pending_parent_request(instance)
        parent_requests = [parent_request] if parent_request is not None else []
    if not parent_requests:
        return False, None
    for parent_request in parent_requests:
        _check_owner(parent_request, user)
    for parent_request in parent_requests:
        _note_change(parent_request, request)
    return True, None


def _check_owner(parent_request, user):
    from .exceptions import ObjectLocked

    if user is None or parent_request.submitted_by_id != user.pk:
        raise ObjectLocked(parent_request, message=(
            f'This belongs to {parent_request.object_repr}, which is new and waiting for approval '
            f'(request #{parent_request.pk}). Until it is decided, only its submitter can change it.'
        ))


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
    # No change log entry of its own for each bump ("Resubmitted for
    # approval" every save was noise); the component's entries are labelled
    # with this request instead (ChangeLogLabelMiddleware).
    parent_request._nbca_log_quietly = True
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
    if model._meta.label_lower == 'dcim.device':
        from dcim.models import Cable

        count = Cable.objects.filter(terminations___device_id=change_request.object_id).distinct().count()
        if count:
            try:
                url = f'{get_action_url(Cable, action="list")}?device_id={change_request.object_id}'
            except Exception:
                url = None
            rows.append((str(Cable._meta.verbose_name_plural).capitalize(), count, url))
    return sorted(rows)


def pending_cable_map():
    """{cable pk: pending new-device request} for cables with an end on a
    pending new device (for marking cable rows in lists)."""
    from core.models import ObjectType
    from dcim.models import CableTermination, Device

    from .models import ChangeRequest

    pending = {
        cr.object_id: cr for cr in ChangeRequest.objects.filter(
            content_type=ObjectType.objects.get_for_model(Device), status=StatusChoices.PENDING,
            action=ActionChoices.CREATE, object_id__isnull=False,
        ).only('pk', 'object_id', 'action', 'content_type', 'object_repr')
    }
    if not pending:
        return {}
    return {
        cable_id: pending[device_id]
        for cable_id, device_id in CableTermination.objects.filter(
            _device_id__in=list(pending),
        ).values_list('cable_id', '_device_id')
    }


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


class ChangeLogLabelMiddleware:
    """
    After a page save that changed components of a pending new device (its
    interfaces, cables, cable ends, IPs, ...), label every change log entry
    that save wrote with the device's request, so the change log shows they
    are part of it and not yet approved.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        parents = getattr(request, _BUMPED_ATTR, None)
        if parents:
            try:
                label_changes(request, parents)
            except Exception:
                pass  # a label is never worth breaking the response
        return response


def label_changes(request, parent_pks):
    from django.contrib.contenttypes.models import ContentType

    from core.models import ObjectChange

    from .models import ChangeRequest, combine_activity_message

    request_id = getattr(request, 'id', None)
    if request_id is None:
        return
    refs = ', '.join(f'#{pk}' for pk in sorted(parent_pks))
    text = f'Part of change request {refs}, waiting for approval'
    entries = ObjectChange.objects.filter(request_id=request_id).exclude(
        changed_object_type=ContentType.objects.get_for_model(ChangeRequest),
    )
    for entry in entries:
        if text in (entry.message or ''):
            continue
        entry.message = combine_activity_message(entry.message, text)
        entry.save(update_fields=['message'])
