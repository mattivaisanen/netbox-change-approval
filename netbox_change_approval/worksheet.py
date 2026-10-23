"""
Field work sheet: the physical jobs approved changes need.

When a request racks, unracks or moves a device, adds or removes a cable,
inserts or removes a module, configures, moves or removes an IP address, or
creates or removes a VM, it gets plain-language tasks ("Mount sw1 (Switch
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


#: Objects named in the task being described, as (text, url), so the page
#: can link them (see refresh()).
_refs = None


def _task(site, rack, text):
    """One task: (site, rack, text, links), the links being the objects
    named since the previous task (see _r)."""
    global _refs
    refs = list(_refs or [])
    if _refs is not None:
        _refs = []
    return site, rack, text, _links_in(text[:300], refs)


def _r(obj):
    """Name `obj` in a task, remembering its link."""
    text = str(obj)
    if _refs is not None and obj is not None and hasattr(obj, 'get_absolute_url'):
        try:
            _refs.append((text, obj.get_absolute_url()))
        except Exception:
            pass
    return text


def _units(position, device_type):
    try:
        start = int(float(position))
    except (TypeError, ValueError):
        return f'U{position}'
    height = int(getattr(device_type, 'u_height', 1) or 1)
    return f'U{start}' if height <= 1 else f'U{start}–{start + height - 1}'


def _place(rack, position, face, device_type):
    text = f'rack {_r(rack)}'
    if position not in (None, ''):
        text += f', {_units(position, device_type)}'
    if face:
        text += f', {face}'
    return text


def _end_label(termination):
    device = getattr(termination, 'device', None)
    if device is None:
        return _r(termination)
    name = getattr(termination, 'name', None)
    if name and _refs is not None and hasattr(termination, 'get_absolute_url'):
        _refs.append((name, termination.get_absolute_url()))
    return f'{_r(device)} {name or termination}'


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
            text = f'Mount {_r(device)} ({device_type}) in {_place(device.rack, device.position, device.face, device_type)}'
        else:
            text = f'Install {_r(device)} ({device_type}) at {_r(device.site)}'
        tasks = [_task(device.site, device.rack, text)]
        # Cables patched to it while it waited came with it (components.py).
        from dcim.models import Cable

        for cable in Cable.objects.filter(terminations___device_id=device.pk).distinct():
            tasks += _cable_tasks(ActionChoices.CREATE, cable)
        tasks += _ips_on(device.interfaces.all(), device.site, device.rack)
        return tasks
    if change_request.action == ActionChoices.DELETE:
        where = _place(device.rack, device.position, device.face, device_type) if device.rack_id else _r(device.site)
        return [_task(device.site, device.rack, f'Remove {_r(device)} from {where}')]

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
    return [_task(device.site, new_rack or device.rack, f'Move {_r(device)} from {old} to {new}')]


def _cable_tasks(action, cable, diff=None):
    a_ends, b_ends = list(cable.a_terminations), list(cable.b_terminations)
    site, rack = _location(a_ends[0]) if a_ends else (None, None)
    if action == ActionChoices.CREATE:
        return [_task(site, rack, f'Patch {_ends(a_ends)} → {_ends(b_ends)}{_cable_detail(cable)}')]
    if action == ActionChoices.DELETE:
        return [_task(site, rack, f'Unpatch and remove cable {_r(cable)}: {_ends(a_ends)} ↔ {_ends(b_ends)}')]
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
    return [_task(site, rack, f'Re-patch cable {cable}: now {_ends(a_ends)} ↔ {_ends(b_ends)}, '
                         f'make it {_ends(new_a)} ↔ {_ends(new_b)}')]


def _assigned_label(obj):
    """'sw1 eth0' for an interface an IP is on."""
    parent = getattr(obj, 'device', None) or getattr(obj, 'virtual_machine', None)
    if parent is None:
        return _r(obj)
    if _refs is not None and hasattr(obj, 'get_absolute_url'):
        _refs.append((obj.name, obj.get_absolute_url()))
    return f'{_r(parent)} {obj.name}'


def _assigned_location(obj):
    parent = getattr(obj, 'device', None) or getattr(obj, 'virtual_machine', None)
    if parent is None:
        return None, None
    return getattr(parent, 'site', None), getattr(parent, 'rack', None)


def _ips_on(interfaces, site, rack):
    """Configure tasks for IP addresses on these interfaces (added while a
    new device or VM waited for approval)."""
    from django.contrib.contenttypes.models import ContentType

    from ipam.models import IPAddress

    interfaces = list(interfaces)
    if not interfaces:
        return []
    ips = IPAddress.objects.filter(
        assigned_object_type=ContentType.objects.get_for_model(interfaces[0]),
        assigned_object_id__in=[i.pk for i in interfaces],
    )
    return [_task(site, rack, f'Configure {_r(ip)} on {_assigned_label(ip.assigned_object)}') for ip in ips]


def _ip_tasks(change_request, ip):
    diff = change_request.diff if isinstance(change_request.diff, dict) else {}
    current = ip.assigned_object
    if change_request.action == ActionChoices.CREATE:
        if current is None:
            return []  # a reserved address: nothing to configure yet
        site, rack = _assigned_location(current)
        return [_task(site, rack, f'Configure {_r(ip)} on {_assigned_label(current)}')]
    if change_request.action == ActionChoices.DELETE:
        if current is None:
            return []
        site, rack = _assigned_location(current)
        return [_task(site, rack, f'Remove {_r(ip)} from {_assigned_label(current)}')]
    if not ({'assigned_object_id', 'assigned_object_type_id'} & set(diff)):
        return []
    from django.contrib.contenttypes.models import ContentType

    def requested(key, value):
        change = diff.get(key)
        return change.get('new') if isinstance(change, dict) else value

    type_id = requested('assigned_object_type_id', ip.assigned_object_type_id)
    object_id = requested('assigned_object_id', ip.assigned_object_id)
    target = None
    if type_id and object_id:
        model = ContentType.objects.get_for_id(type_id).model_class()
        target = model.objects.filter(pk=object_id).first() if model else None
    site, rack = _assigned_location(target or current) if (target or current) else (None, None)
    if current is not None and target is not None:
        text = f'Move {_r(ip)} from {_assigned_label(current)} to {_assigned_label(target)}'
    elif target is not None:
        text = f'Configure {_r(ip)} on {_assigned_label(target)}'
    elif current is not None:
        text = f'Remove {_r(ip)} from {_assigned_label(current)}'
    else:
        return []
    return [_task(site, rack, text)]


def _vm_tasks(change_request, vm):
    site = getattr(vm, 'site', None)
    if change_request.action == ActionChoices.CREATE:
        where = f' on cluster {_r(vm.cluster)}' if vm.cluster_id else ''
        return [_task(site, None, f'Create VM {_r(vm)}{where}')] + _ips_on(vm.interfaces.all(), site, None)
    if change_request.action == ActionChoices.DELETE:
        return [_task(site, None, f'Remove VM {_r(vm)}')]
    return []


def _module_tasks(change_request, module):
    site, rack = _location(module.device)
    if change_request.action == ActionChoices.CREATE:
        return [_task(site, rack, f'Insert module {module.module_type} into {_r(module.device)} bay {_r(module.module_bay)}')]
    if change_request.action == ActionChoices.DELETE:
        return [_task(site, rack, f'Remove module {module.module_type} from {_r(module.device)} bay {_r(module.module_bay)}')]
    return []


def describe(change_request):
    """[(site, rack, text, links)] physical jobs for a request; [] if none."""
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
    if label == 'ipam.ipaddress':
        return _ip_tasks(change_request, target)
    if label == 'virtualization.virtualmachine':
        return _vm_tasks(change_request, target)
    # Sites, locations, racks and the like already exist on the ground.
    return []


# ---- Keeping tasks up to date --------------------------------------------------


def _links_in(text, refs):
    """The (text, url) pairs named in `text`, as links left to right without
    overlapping: each step takes the name that occurs next after the last
    one taken (the longer name on a tie), so "sw1 eth1 -> pp1 1" links eth1
    before the lone 1."""
    remaining = [(label, url) for label, url in dict.fromkeys(refs) if label]
    links, end = [], 0
    while remaining:
        best = None
        for index, (label, url) in enumerate(remaining):
            position = text.find(label, end)
            if position == -1:
                continue
            key = (position, -len(label))
            if best is None or key < best[0]:
                best = (key, index)
        if best is None:
            break
        (position, _negative_length), index = best
        label, url = remaining.pop(index)
        links.append([label, url])
        end = position + len(label)
    return links


def refresh(change_request):
    """Rewrite a request's tasks from what it asks for now."""
    global _refs
    _refs = []
    try:
        tasks = describe(change_request)
    except Exception:
        logger.exception('Could not describe work for change request #%s', change_request.pk)
        return
    finally:
        _refs = None
    WorkTask.objects.filter(change_request=change_request, done_at__isnull=True).delete()
    WorkTask.objects.bulk_create([
        WorkTask(change_request=change_request, site=site, rack=rack, text=text[:300], links=links)
        for site, rack, text, links in tasks
    ])


def _saved(sender, instance, created, **kwargs):
    if not enabled():
        return
    # Written while the request waits (everything it names still exists), or
    # at once for an auto-approved request (saved just before its write).
    # Written again when a new object is approved: whatever was added to it
    # while it waited (cables patched to a new device, say) is all there now.
    # A change saved outside a transaction (the edit form validates before
    # NetBox opens one) runs the refresh at once, before the component being
    # saved exists, so this last pass is what makes the list complete.
    approved_create = (
        instance.status == StatusChoices.APPLIED and instance.action == ActionChoices.CREATE
    )
    if instance.status == StatusChoices.PENDING or approved_create or (
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
