"""
"Where does this object live?" rows for the change request detail page.

An approver looking at a new device mostly wants to know *where* it goes
(site, location, rack, position) before reading the rest of the diff. This
module picks those parent/placement fields out of the request — generically,
by field name, so it covers devices, racks, modules, VMs, prefixes, IPs,
circuit terminations etc. without per-model code — and resolves them to real
objects so the template can link to them.

Where the values come from:
- create: the proposed data (the diff snapshot).
- delete: the snapshot of the object being removed.
- update: the live object; for a placement field the update itself changes,
  the old and new values from the diff (shown as "old → new").
"""
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist

from .models import ActionChoices

#: Parent/placement fields, outermost first. Only the ones the model actually
#: has are shown. `scope`, `assigned_object` and `termination` are generic
#: foreign keys (prefix/cluster scope, IP assignment, circuit termination).
PLACEMENT_FIELDS = (
    'region',
    'site_group',
    'site',
    'location',
    'rack',
    'position',
    'face',
    'scope',
    'cluster',
    'virtual_machine',
    'device',
    'module_bay',
    'module',
    'power_panel',
    'circuit',
    'termination',
    'group',
    'parent',
    'assigned_object',
)

_MISSING = object()


def _field(model, name):
    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None


def _keys(field):
    """The diff/attribute keys a field's value is stored under."""
    if isinstance(field, GenericForeignKey):
        return (f'{field.ct_field}_id', field.fk_field)
    return (field.attname,)


def _format_number(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if number == number.to_integral_value():
        return str(int(number))
    return str(number.normalize())


def _resolve(field, raw):
    """Turn stored raw value(s) into something displayable: a model instance
    (the template links it) or a string. None means "not set"."""
    if isinstance(field, GenericForeignKey):
        ct_id, obj_id = raw
        if ct_id is None or obj_id is None:
            return None
        try:
            ct = ContentType.objects.get_for_id(ct_id)
            return ct.get_object_for_this_type(pk=obj_id)
        except Exception:  # missing content type or object
            return f'#{obj_id} (not found)'

    (value,) = raw
    if value is None or value == '':
        return None
    if field.is_relation:
        obj = field.related_model.objects.filter(pk=value).first()
        return obj if obj is not None else f'#{value} (not found)'
    if field.choices:
        return str(dict(field.flatchoices).get(value, value))
    if field.get_internal_type() in ('DecimalField', 'FloatField'):
        return _format_number(value)
    return str(value)


def placement_rows(change_request):
    """[{'label', 'value', 'new', 'changed'}] for the detail page's left panel.
    `new` is only meaningful when `changed` is True (an update moving the
    object); otherwise `value` is the one value to show."""
    if change_request.content_type_id is None:
        return []
    model = change_request.content_type.model_class()
    if model is None:
        return []
    diff = change_request.diff if isinstance(change_request.diff, dict) else {}
    obj = change_request.target_object
    is_update = change_request.action == ActionChoices.UPDATE

    def current(key):
        if not is_update and key in diff:
            return diff[key]
        if obj is not None:
            return getattr(obj, key, None)
        if is_update and isinstance(diff.get(key), dict):
            return diff[key].get('old')
        return None

    rows = []
    for name in PLACEMENT_FIELDS:
        field = _field(model, name)
        if field is None:
            continue
        # Only real columns (or generic FKs) — skip reverse relations and M2Ms
        # that happen to share one of these names.
        if not isinstance(field, GenericForeignKey) and (not field.concrete or field.many_to_many):
            continue
        keys = _keys(field)
        label = str(getattr(field, 'verbose_name', None) or name.replace('_', ' '))
        label = label[:1].upper() + label[1:]

        changed = is_update and any(isinstance(diff.get(k), dict) for k in keys)
        if changed:
            old_raw = tuple(diff[k]['old'] if isinstance(diff.get(k), dict) else current(k) for k in keys)
            new_raw = tuple(diff[k]['new'] if isinstance(diff.get(k), dict) else current(k) for k in keys)
            old, new = _resolve(field, old_raw), _resolve(field, new_raw)
            if old is None and new is None:
                continue
            rows.append({'label': label, 'value': old, 'new': new, 'changed': True})
        else:
            value = _resolve(field, tuple(current(k) for k in keys))
            if value is None:
                continue
            rows.append({'label': label, 'value': value, 'new': None, 'changed': False})
    return rows


def rack_previews(change_request):
    """
    Racks to show on a device request's page, when the request puts a device
    into a rack, takes one out, or moves it: [(rack, face, caption)].
    Empty for anything else (a rename, say), so the page stays short.
    """
    from dcim.models import Device, Rack

    model = change_request.content_type.model_class() if change_request.content_type_id else None
    if model is not Device:
        return []
    diff = change_request.diff if isinstance(change_request.diff, dict) else {}
    device = change_request.target_object

    if change_request.action in (ActionChoices.CREATE, ActionChoices.DELETE):
        source = device if device is not None else None
        rack_id = getattr(source, 'rack_id', None) or diff.get('rack_id')
        position = getattr(source, 'position', None) if source is not None else diff.get('position')
        face = getattr(source, 'face', None) or diff.get('face') or 'front'
        rack = Rack.objects.filter(pk=rack_id).first() if rack_id else None
        if rack is None or position in (None, ''):
            return []
        caption = 'Goes here' if change_request.action == ActionChoices.CREATE else 'Removed from here'
        return [(rack, face, caption)]

    if not ({'rack_id', 'position', 'face'} & set(diff)) or device is None:
        return []

    def requested(key, current):
        change = diff.get(key)
        return change.get('new') if isinstance(change, dict) else current

    previews = []
    new_rack_id = requested('rack_id', device.rack_id)
    new_face = requested('face', device.face) or 'front'
    if device.rack_id and device.position is not None:
        caption = 'Now' if new_rack_id != device.rack_id else 'Now and requested'
        previews.append((device.rack, device.face or 'front', caption))
    if new_rack_id and new_rack_id != device.rack_id and requested('position', device.position) not in (None, ''):
        rack = Rack.objects.filter(pk=new_rack_id).first()
        if rack is not None:
            previews.append((rack, new_face, 'Requested'))
    return previews
