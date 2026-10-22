"""
How pending objects are marked in object lists and rack elevations.

Default ('by_action'): what is pending is visible at a glance.
- New objects: green stripe and a "+ new" chip.
- Edits: blue stripe, an "edit pending" chip, and each changed cell shows
  the current value struck through next to the requested one.
- Deletes: red stripe, a "delete pending" chip, and the row greyed out and
  struck through.
Colors match NetBox's own change log (and this plugin's request list). The
chip links to the request.

Set PLUGINS_CONFIG['netbox_change_approval']['pending_row_style'] = 'outline'
to go back to the earlier look: one yellow outline for every pending row,
whatever the action.
"""
from django.core.exceptions import FieldDoesNotExist

from .models import ActionChoices, ChangeRequest, StatusChoices

STYLE_BY_ACTION = 'by_action'
STYLE_OUTLINE = 'outline'

#: Tabler's own default colors, used where a CSS variable may not exist (the
#: rack elevation is its own SVG document).
ACTION_COLORS = {
    ActionChoices.CREATE: ('var(--tblr-green, #2fb344)', '#2fb344'),
    ActionChoices.UPDATE: ('var(--tblr-blue, #206bc4)', '#206bc4'),
    ActionChoices.DELETE: ('var(--tblr-red, #d63939)', '#d63939'),
}


def style():
    from netbox.plugins.utils import get_plugin_config

    value = get_plugin_config('netbox_change_approval', 'pending_row_style')
    return STYLE_OUTLINE if value == STYLE_OUTLINE else STYLE_BY_ACTION


def by_action():
    return style() == STYLE_BY_ACTION


def pending_requests(content_type, object_ids=None):
    """{object pk: pending ChangeRequest} for one object type."""
    queryset = ChangeRequest.objects.filter(
        content_type=content_type, status=StatusChoices.PENDING, object_id__isnull=False,
    )
    if object_ids is not None:
        queryset = queryset.filter(object_id__in=object_ids)
    return {cr.object_id: cr for cr in queryset.only('pk', 'object_id', 'action', 'diff', 'content_type')}


def _display(model, key, value):
    """A requested value as text, the way the list would show it."""
    if value is None or value == '' or value == []:
        return '—'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    field_name = key[:-3] if key.endswith('_id') else key
    try:
        field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        field = None
    if field is not None and not field.is_relation and getattr(field, 'choices', None):
        labels = {str(k): str(v) for k, v in field.flatchoices}
        return labels.get(str(value), str(value))
    from .views import _resolve_display

    shown = _resolve_display(model, key, value)
    if isinstance(shown, (list, tuple)):
        return ', '.join(str(v) for v in shown) or '—'
    return str(shown)


def column_changes(change_request, model, column_names):
    """
    For a pending update: {column name: requested value as text}, for the
    changed fields that this table has a column for. Custom fields map to
    their cf_<name> columns.
    """
    diff = change_request.diff if isinstance(change_request.diff, dict) else {}
    changes = {}
    for key, change in diff.items():
        if not isinstance(change, dict):
            continue
        new = change.get('new')
        if key == 'custom_field_data':
            old = change.get('old') or {}
            for name, value in (new or {}).items():
                if (old or {}).get(name) != value and f'cf_{name}' in column_names:
                    changes[f'cf_{name}'] = '—' if value in (None, '') else str(value)
            continue
        name = key[:-3] if key.endswith('_id') else key
        if name in column_names:
            changes[name] = _display(model, key, new)
    return changes


def change_summary(change_request, model):
    """One line listing every changed field, for the chip's tooltip."""
    from .views import _field_label

    diff = change_request.diff if isinstance(change_request.diff, dict) else {}
    parts = []
    for key, change in sorted(diff.items()):
        if not isinstance(change, dict):
            continue
        if key == 'custom_field_data':
            for name, value in (change.get('new') or {}).items():
                if (change.get('old') or {}).get(name) != value:
                    parts.append(f'{name}: {value if value not in (None, "") else chr(0x2014)}')
            continue
        parts.append(f'{_field_label(model, key)}: {_display(model, key, change.get("new"))}')
    return '; '.join(parts)
