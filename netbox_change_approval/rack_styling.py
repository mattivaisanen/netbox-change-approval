"""
Border highlight for devices with a pending ChangeRequest, drawn into the rack
elevation itself. table_styling.py's CSS-injection trick doesn't reach here:
rack.html embeds the elevation via `<object data="...rack-elevation/?render=svg">`
(templates/dcim/inc/rack_elevation.html, NetBox 4.2) — a *separate* SVG document
fetched from the API, not inline DOM, so nothing added via a
PluginTemplateExtension.head() <style> tag on the host page can style it.

Instead this monkeypatches dcim.svg.racks.RackElevationSVG the same way
table_styling.py patches NetBoxTable.configure(): wrap _setup_drawing() to add
one extra CSS rule to the SVG's own embedded <style> (it already builds one
this way from STATIC_ROOT/rack_elevation.css), and wrap _draw_device() to draw
an extra unfilled rect on top of any device with a pending ChangeRequest.
"""
from core.models import ObjectType

from .models import ActionChoices, ChangeRequest, StatusChoices

#: The elevation is a separate SVG document (see module docstring): NetBox's
#: page variables such as --tblr-warning don't reach it. So the SVG gets its
#: own color variables, set to Tabler's default warning/green/blue/red, and
#: every rule below uses them. Alpha tints use color-mix().
SVG_COLOR_VARIABLES = (
    ':root { --nbca-pending: #f59f00; --nbca-create: #2fb344; '
    '--nbca-update: #206bc4; --nbca-delete: #d63939; }'
)
PENDING_DEVICE_STROKE = 'var(--nbca-pending)'

_patched = False


def _pending_device_actions(rack):
    """{device pk: pending action} for the devices in this rack."""
    content_type = ObjectType.objects.get_for_model(rack.devices.model)
    return dict(
        ChangeRequest.objects.filter(
            content_type=content_type,
            status=StatusChoices.PENDING,
            object_id__in=rack.devices.values_list('pk', flat=True),
        ).values_list('object_id', 'action')
    )


def patch_rack_elevation_svg():
    """Idempotent, same reasoning as table_styling.patch_netbox_table()."""
    global _patched
    if _patched:
        return

    from svgwrite.shapes import Rect

    from dcim.svg.racks import RackElevationSVG

    original_setup_drawing = RackElevationSVG._setup_drawing
    original_draw_device = RackElevationSVG._draw_device

    def _setup_drawing(self):
        drawing = original_setup_drawing(self)
        from . import pending_marks

        rules = SVG_COLOR_VARIABLES + (
            f' .nbca-pending-device {{ fill: none; stroke: {PENDING_DEVICE_STROKE}; '
            f'stroke-width: 2px; pointer-events: none; }}'
        )
        if pending_marks.by_action():
            # Same colors as the object lists: green new, blue edit, red
            # (dashed) delete. See pending_marks.py.
            for action in ActionChoices.values:
                rules += f' .nbca-pending-{action} {{ stroke: var(--nbca-{action}); stroke-width: 3px; }}'
            rules += ' .nbca-pending-delete { stroke-dasharray: 6 3; }'
            rules += (
                ' .nbca-requested-move { fill: color-mix(in srgb, var(--nbca-update) 13%, transparent);'
                ' stroke: var(--nbca-update); stroke-width: 2px;'
                ' stroke-dasharray: 4 3; pointer-events: none; }'
                ' .nbca-requested-label { fill: var(--nbca-update); font-size: 12px; font-weight: 600;'
                ' text-anchor: middle; dominant-baseline: middle; pointer-events: none; }'
            )
        drawing.defs.add(drawing.style(rules))
        # Cached per-instance (not per-call): _draw_device fires once per
        # device per render(), and render() is one instance per face/request —
        # one query here beats one per device drawn.
        self._nbca_pending_actions = _pending_device_actions(self.rack)
        return drawing

    def _draw_device(self, device, coords, size, color=None, image=None):
        original_draw_device(self, device, coords, size, color=color, image=image)
        action = getattr(self, '_nbca_pending_actions', {}).get(device.pk)
        if action is not None:
            # Inset 1px and rounded to read as a slight outline sitting inside
            # the device's own slot, not a line straddling the boundary with
            # its neighbor — same look pending_row_styles.html gives table rows.
            inset = 1
            self.drawing.add(Rect(
                (coords[0] + inset, coords[1] + inset),
                (size[0] - inset * 2, size[1] - inset * 2),
                rx=2, ry=2,
                class_=f'nbca-pending-device nbca-pending-{action}',
            ))

    original_draw_face = RackElevationSVG.draw_face

    def draw_face(self, face, opposite=False):
        original_draw_face(self, face, opposite=opposite)
        try:
            _draw_requested_moves(self, face, Rect)
        except Exception:
            pass  # never break the elevation over a preview

    RackElevationSVG._setup_drawing = _setup_drawing
    RackElevationSVG._draw_device = _draw_device
    RackElevationSVG.draw_face = draw_face
    _patched = True


def requested_moves(rack):
    """
    Pending edits that move a device into `rack` (or to another position or
    face in it): [(device, position, face)] with the requested values.
    """
    from dcim.models import Device

    content_type = ObjectType.objects.get_for_model(Device)
    moves = []
    for change_request in ChangeRequest.objects.filter(
        content_type=content_type, status=StatusChoices.PENDING, action='update',
    ):
        diff = change_request.diff if isinstance(change_request.diff, dict) else {}
        if not ({'rack_id', 'position', 'face'} & set(diff)):
            continue
        device = Device.objects.filter(pk=change_request.object_id).select_related('device_type').first()
        if device is None:
            continue

        def requested(key, current):
            change = diff.get(key)
            return change.get('new') if isinstance(change, dict) else current

        rack_id = requested('rack_id', device.rack_id)
        position = requested('position', device.position)
        if rack_id != rack.pk or position in (None, ''):
            continue
        moves.append((device, position, requested('face', device.face)))
    return moves


def _draw_requested_moves(svg, face, Rect):
    """Dashed outline where a pending edit would move a device to: the
    approver sees where it lands next to its new neighbours."""
    from decimal import Decimal

    from svgwrite.text import Text

    from . import pending_marks

    if not pending_marks.by_action():
        return
    for device, position, requested_face in requested_moves(svg.rack):
        full_depth = getattr(device.device_type, 'is_full_depth', False)
        if requested_face != face and not full_depth:
            continue
        height = device.device_type.u_height or 1
        coords = svg._get_device_coords(Decimal(str(position)), height)
        size = (svg.unit_width, int(svg.unit_height * height))
        svg.drawing.add(Rect(
            (coords[0] + 1, coords[1] + 1), (size[0] - 2, size[1] - 2), rx=2, ry=2,
            class_='nbca-requested-move',
        ))
        svg.drawing.add(Text(
            f'{device.name or device} (requested)',
            insert=(coords[0] + size[0] / 2, coords[1] + size[1] / 2),
            class_='nbca-requested-label',
        ))
