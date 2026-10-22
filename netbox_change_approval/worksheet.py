"""
Field work sheet: the physical jobs approved changes need.

When a request racks, unracks or moves a device, adds or removes a cable, or
inserts or removes a module, it gets plain-language tasks ("Mount sw1 (Switch
48) in rack A3, U20-21, front", "Patch sw1 eth1 -> pp1 port 3"). The text is
written while the request waits, when everything it names still exists, and
rewritten when the request is updated. A task appears on the Work Sheet page
once its request is applied; a technician ticks it off on site, which records
who and when. Rack and site pages show how many tasks are still open.

All of it lives in this module, WorkTask (models.py), migration 0011,
templates/netbox_change_approval/worksheet.html and the Work Sheet menu
item. PLUGINS_CONFIG['netbox_change_approval']['work_sheets'] = False turns it
off.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save

from .models import ActionChoices, ChangeRequest, StatusChoices, WorkTask

logger = logging.getLogger('netbox_change_approval.worksheet')


def enabled():
    from .plugin_settings import get_setting

    return get_setting('work_sheets') is not False


# ---- Describing jobs ---------------------------------------------------------


def _units(position, device_type):
    try:
        start = int(float(position))
    except (TypeError, ValueError):
        return f'U{position}'
    height = int(getattr(device_type, 'u_height', 1) or 1)
    return f'U{start}' if height <= 1 else f'U{start}–{start + height - 1}'


def _place(rack, position, face, device_type):
    text = f'rack {rack}'
    if position not in (None, ''):
        text += f', {_units(position, device_type)}'
    if face:
        text += f', {face}'
    return text


def _end_label(termination):
    device = getattr(termination, 'device', None)
    name = getattr(termination, 'name', None) or str(termination)
    return f'{device} {name}' if device is not None else str(termination)


def _ends(objects):
    return ', '.join(_end_label(obj) for obj in objects) or '?'


def _cable_detail(cable):
    parts = [p for p in (cable.get_type_display() if cable.type else '',
                         f'{cable.length:g} {cable.length_unit}' if cable.length else '') if p]
    return f' ({", ".join(parts)})' if parts else ''


def _location(obj):
    """(site, rack) of a device, or of the device a port/termination is on."""
    device = obj if obj._meta.label_lower == 'dcim.device' else getattr(obj, 'device', None)
    if device is None:
        return None, None
    return device.site, device.rack


def _device_tasks(change_request, device):
    from dcim.models import Rack

    diff = change_request.diff if isinstance(change_request.diff, dict) else {}
    device_type = device.device_type
    if change_request.action == ActionChoices.CREATE:
        if device.rack_id:
            text = f'Mount {device} ({device_type}) in {_place(device.rack, device.position, device.face, device_type)}'
        else:
            text = f'Install {device} ({device_type}) at {device.site}'
        tasks = [(device.site, device.rack, text)]
        # Cables patched to it while it waited came with it (components.py).
        from dcim.models import Cable

        for cable in Cable.objects.filter(terminations___device_id=device.pk).distinct():
            tasks += _cable_tasks(ActionChoices.CREATE, cable)
        return tasks
    if change_request.action == ActionChoices.DELETE:
        where = _place(device.rack, device.position, device.face, device_type) if device.rack_id else str(device.site)
        return [(device.site, device.rack, f'Remove {device} from {where}')]

    if not ({'rack_id', 'position', 'face'} & set(diff)):
        return []

    def requested(key, current):
        change = diff.get(key)
        return change.get('new') if isinstance(change, dict) else current

    new_rack_id = requested('rack_id', device.rack_id)
    new_rack = Rack.objects.filter(pk=new_rack_id).first() if new_rack_id else None
    old = _place(device.rack, device.position, device.face, device_type) if device.rack_id else 'no rack'
    new = _place(new_rack, requested('position', device.position), requested('face', device.face), device_type) \
        if new_rack else 'no rack'
    return [(device.site, new_rack or device.rack, f'Move {device} from {old} to {new}')]


def _cable_tasks(action, cable, diff=None):
    a_ends, b_ends = list(cable.a_terminations), list(cable.b_terminations)
    site, rack = _location(a_ends[0]) if a_ends else (None, None)
    if action == ActionChoices.CREATE:
        return [(site, rack, f'Patch {_ends(a_ends)} → {_ends(b_ends)}{_cable_detail(cable)}')]
    if action == ActionChoices.DELETE:
        return [(site, rack, f'Unpatch and remove cable {cable}: {_ends(a_ends)} ↔ {_ends(b_ends)}')]
    diff = diff or {}
    if not ({'a_terminations', 'b_terminations'} & set(diff)):
        return []
    from .serialization import _resolve_termination_refs

    def side(key, current):
        change = diff.get(key)
        if not isinstance(change, dict):
            return current
        try:
            return _resolve_termination_refs(change.get('new'))
        except Exception:
            return current

    new_a, new_b = side('a_terminations', a_ends), side('b_terminations', b_ends)
    return [(site, rack, f'Re-patch cable {cable}: now {_ends(a_ends)} ↔ {_ends(b_ends)}, '
                         f'make it {_ends(new_a)} ↔ {_ends(new_b)}')]


def _module_tasks(change_request, module):
    site, rack = _location(module.device)
    if change_request.action == ActionChoices.CREATE:
        return [(site, rack, f'Insert module {module.module_type} into {module.device} bay {module.module_bay}')]
    if change_request.action == ActionChoices.DELETE:
        return [(site, rack, f'Remove module {module.module_type} from {module.device} bay {module.module_bay}')]
    return []


def describe(change_request):
    """[(site, rack, text)] physical jobs for a request; [] if none."""
    target = change_request.target_object
    if target is None:
        return []
    label = target._meta.label_lower
    if label == 'dcim.device':
        return _device_tasks(change_request, target)
    if label == 'dcim.cable':
        return _cable_tasks(change_request.action, target, change_request.diff)
    if label == 'dcim.module':
        return _module_tasks(change_request, target)
    return []


# ---- Keeping tasks up to date --------------------------------------------------


def refresh(change_request):
    """Rewrite a request's tasks from what it asks for now."""
    try:
        tasks = describe(change_request)
    except Exception:
        logger.exception('Could not describe work for change request #%s', change_request.pk)
        return
    WorkTask.objects.filter(change_request=change_request, done_at__isnull=True).delete()
    WorkTask.objects.bulk_create([
        WorkTask(change_request=change_request, site=site, rack=rack, text=text[:300])
        for site, rack, text in tasks
    ])


def _saved(sender, instance, created, **kwargs):
    if not enabled():
        return
    # Written while the request waits (everything it names still exists), or
    # at once for an auto-approved request (saved just before its write).
    if instance.status == StatusChoices.PENDING or (
        instance.status == StatusChoices.APPLIED and instance.auto_approved and created
    ):
        pk = instance.pk
        transaction.on_commit(lambda: _refresh_pk(pk))


def _refresh_pk(pk):
    change_request = ChangeRequest.objects.filter(pk=pk).first()
    if change_request is not None:
        refresh(change_request)


def open_tasks():
    return WorkTask.objects.filter(change_request__status=StatusChoices.APPLIED, done_at__isnull=True)


def connect():
    post_save.connect(_saved, sender=ChangeRequest, dispatch_uid='netbox_change_approval:worksheet')


# ---- Rack and site pages -------------------------------------------------------


def _panel_class():
    from netbox.plugins import PluginTemplateExtension

    class OpenWorkPanel(PluginTemplateExtension):
        """"N open work tasks" on rack and site pages."""
        models = ['dcim.rack', 'dcim.site']

        def right_page(self):
            if not enabled():
                return ''
            obj = self.context.get('object')
            if obj is None:
                return ''
            field = 'rack' if obj._meta.label_lower == 'dcim.rack' else 'site'
            count = open_tasks().filter(**{field: obj}).count()
            if not count:
                return ''
            return self.render('netbox_change_approval/worksheet_panel.html', {
                'count': count, 'field': field, 'object': obj,
            })

    return OpenWorkPanel
