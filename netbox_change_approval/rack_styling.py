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

from .models import ChangeRequest, StatusChoices

#: var(--tblr-warning) is what pending_row_styles.html uses on the *host* page,
#: but this SVG is a separate document (see module docstring) with its own
#: stylesheet bundle, compiled straight from bootstrap's variables.scss rather
#: than inheriting the host page's cascade — unclear whether --tblr-warning
#: even exists in that bundle. The CSS fallback covers it either way: resolves
#: to the real theme color if the var happens to be defined, else this hex
#: (Tabler's own default --tblr-warning) if not.
PENDING_DEVICE_STROKE = 'var(--tblr-warning, #f59f00)'

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

        rules = (
            f'.nbca-pending-device {{ fill: none; stroke: {PENDING_DEVICE_STROKE}; '
            f'stroke-width: 2px; pointer-events: none; }}'
        )
        if pending_marks.by_action():
            # Same colors as the object lists: green new, blue edit, red
            # (dashed) delete. See pending_marks.py.
            for action, (_var, hex_color) in pending_marks.ACTION_COLORS.items():
                rules += f' .nbca-pending-{action} {{ stroke: {hex_color}; stroke-width: 3px; }}'
            rules += ' .nbca-pending-delete { stroke-dasharray: 6 3; }'
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

    RackElevationSVG._setup_drawing = _setup_drawing
    RackElevationSVG._draw_device = _draw_device
    _patched = True
