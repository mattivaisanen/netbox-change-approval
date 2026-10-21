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
    from core.models import ObjectType

    from .discovery import is_gated_model
    from .models import ChangeRequest, StatusChoices

    model = getattr(table._meta, 'model', None)
    if model is None or not is_gated_model(model):
        return

    content_type = ObjectType.objects.get_for_model(model)
    pending_pks = set(
        ChangeRequest.objects.filter(
            content_type=content_type, status=StatusChoices.PENDING,
        ).values_list('object_id', flat=True)
    )
    if not pending_pks:
        return

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
    # parameter (computed_values does pass it) — `existing_class`/`pending_pks`
    # must come from the closure instead.
    def _row_class(record, table):
        base = _resolve(existing_class, record=record, table=table) or ''
        if getattr(record, 'pk', None) in pending_pks:
            return f'{base} {PENDING_ROW_CSS_CLASS}'.strip()
        return base

    table.row_attrs = {**table.row_attrs, 'class': _row_class}


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
