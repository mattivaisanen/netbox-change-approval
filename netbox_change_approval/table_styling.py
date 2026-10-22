"""
Row highlighting for anything with a pending ChangeRequest, on *any* model's own
list view — no per-model table code, so a pending row stands out without any
column setup. See DEVELOPER.md
"Row highlighting via a monkeypatched NetBoxTable.configure()" for the full
reasoning and the risk this carries — there is no PluginTemplateExtension hook
for per-row list content (list_buttons() only adds buttons near the Add/Import
controls — see template_content.py's own list-view badge for that half of this).
"""
from django_tables2.utils import call_with_appropriate

#: A yellow outline, not a filled background (Bootstrap's own `table-warning`
#: fills the row, which read as too heavy next to the list-view badge's
#: `btn-outline-warning` pill — see template_content.PendingRowStyles for the
#: actual CSS, injected via head() since this is just a class name).
PENDING_ROW_CSS_CLASS = 'nbca-pending-row'

_patched = False


def _resolve(value, **kwargs):
    if callable(value):
        return call_with_appropriate(value, kwargs)
    return value


def _highlight_pending_rows(table):
    import json

    from core.models import ObjectType

    from . import pending_marks
    from .discovery import is_gated_model
    from .models import ActionChoices

    model = getattr(table._meta, 'model', None)
    if model is None or not is_gated_model(model):
        return

    content_type = ObjectType.objects.get_for_model(model)
    pending = pending_marks.pending_requests(content_type)
    by_action = pending_marks.by_action()

    # Components of a pending new device/VM are new with it (components.py).
    parent_maps = []
    if by_action:
        from . import components

        if components.enabled():
            for field_name in components.PARENT_FIELDS:
                try:
                    field = model._meta.get_field(field_name)
                except Exception:
                    continue
                if field.is_relation and field.many_to_one:
                    parent_map = components.pending_parent_map(model, field_name)
                    if parent_map:
                        parent_maps.append((field.attname, parent_map))
            if model._meta.label_lower == 'dcim.cable':
                cable_map = components.pending_cable_map()
                if cable_map:
                    parent_maps.append(('pk', cable_map))
    if not pending and not parent_maps:
        return

    def _lookup(record):
        change_request = pending.get(getattr(record, 'pk', None))
        if change_request is not None:
            return change_request
        for attname, parent_map in parent_maps:
            parent_request = parent_map.get(getattr(record, attname, None))
            if parent_request is not None:
                return parent_request
        return None

    # Preserve whatever row_attrs['class'] this specific table might already
    # define (rare, but several NetBox core tables do use it) rather than
    # clobbering it — django_tables2's own even/odd striping is handled
    # separately (BoundRow.attrs appends it after row_attrs), so that one's
    # already safe regardless.
    existing_class = table.row_attrs.get('class')

    # NOT `def _row_class(record, table=table, existing_class=existing_class)`:
    # computed_values() invokes row_attrs callables through call_with_appropriate(),
    # which inspects the callable's parameter *names* and requires every one of
    # them to be present in the kwargs it was given (table=, record=) — it does
    # not know or care that a parameter has a default, so an extra parameter like
    # `existing_class` (never in that kwargs dict) makes it silently return None
    # instead of calling the function at all. A first version did exactly this and
    # the highlight silently never appeared. `table` is fine to keep as a
    # parameter (computed_values does pass it) — `existing_class`/`pending`
    # must come from the closure instead.
    def _row_class(record, table):
        base = _resolve(existing_class, record=record, table=table) or ''
        change_request = _lookup(record)
        if change_request is None:
            return base
        extra = PENDING_ROW_CSS_CLASS
        if by_action:
            extra = f'{extra} nbca-pending-{change_request.action}'
        return f'{base} {extra}'.strip()

    row_attrs = {**table.row_attrs, 'class': _row_class}

    if by_action:
        column_names = {column.name for column in table.columns}
        changes_cache = {}

        def _changes(change_request):
            if change_request.pk not in changes_cache:
                changes_cache[change_request.pk] = (
                    pending_marks.column_changes(change_request, model, column_names),
                    pending_marks.change_summary(change_request, model),
                )
            return changes_cache[change_request.pk]

        def _action(record):
            change_request = _lookup(record)
            return change_request.action if change_request else None

        def _url(record):
            change_request = _lookup(record)
            return change_request.get_absolute_url() if change_request else None

        def _cells(record):
            change_request = _lookup(record)
            if change_request is None or change_request.action != ActionChoices.UPDATE:
                return None
            return json.dumps(_changes(change_request)[0])

        def _summary(record):
            change_request = _lookup(record)
            if change_request is None:
                return None
            if change_request.object_id != getattr(record, 'pk', None) or change_request.content_type_id != content_type.pk:
                return f'Part of new {change_request.object_repr} (request #{change_request.pk})'
            if change_request.action != ActionChoices.UPDATE:
                return None
            return _changes(change_request)[1]

        row_attrs.update({
            'data-nbca-action': _action,
            'data-nbca-url': _url,
            'data-nbca-changes': _cells,
            'data-nbca-summary': _summary,
        })
        # Name each cell after its column, so the page script can find the
        # changed ones (django-tables2 renders bare <td>s).
        if any(cr.action == ActionChoices.UPDATE for cr in pending.values()):
            for column in table.columns:
                attrs = dict(column.column.attrs or {})
                td = dict(attrs.get('td') or {})
                td['data-nbca-col'] = column.name
                attrs['td'] = td
                column.column.attrs = attrs

    table.row_attrs = row_attrs


def patch_netbox_table():
    """
    Wraps NetBoxTable.configure() — the one shared method every model-specific
    Table class calls via super(), right before rendering, regardless of app or
    plugin — so it also applies _highlight_pending_rows() afterward. Idempotent
    (safe to call more than once, e.g. if ready() somehow runs twice in one
    process). The try/except inside the wrapper means a bug here can only ever
    cost the highlight, never break the page it would've decorated.
    """
    global _patched
    if _patched:
        return

    from netbox.tables import NetBoxTable

    original_configure = NetBoxTable.configure

    def _configure(self, request):
        original_configure(self, request)
        try:
            _highlight_pending_rows(self)
        except Exception:
            pass

    NetBoxTable.configure = _configure
    _patched = True
