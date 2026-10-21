"""
Bulk edit, bulk import and bulk delete: one request per object.

NetBox's bulk views process every selected object inside one
transaction.atomic(). Before this module, the first object that needed
approval raised out of that loop: NetBox rolled the whole transaction back
(taking our just-saved ChangeRequest with it) and never looked at the other
objects. The user was told "submitted for approval", but nothing was queued.

While ChangeApprovalMiddleware has a BatchCollector open (see
context.batch_collector), each object's write runs in its own savepoint:

- Bulk edit: BulkEditView._update_objects() is called once per object. The
  validator raises ChangeApprovalRequired for an object that needs approval;
  we roll back that object's savepoint, record its request, and move on.
- Bulk delete: each gated model's delete() runs in a savepoint the same way
  (signals.gate_delete raises ChangeApprovalRequired from pre_delete).
- Bulk import: validation happens in model_form.is_valid(), which we can't
  wrap per record. The validator instead parks the exception on the instance
  (BatchCollector.HOLD mode) and BulkImportView._save_object() skips saving it.

Objects that need no approval (bypass, auto-approval, no-op) are saved as
usual. Requests are saved inside NetBox's own transaction, after the object's
savepoint rolled back, so they persist exactly when the bulk action succeeds.
Events queued for a rolled-back object (webhooks, event rules) are dropped too.
The middleware replaces NetBox's "Updated N objects" message with a summary.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from django.db import router, transaction

from netbox.context import events_queue

from .context import batch_collector
from .discovery import is_gated_model
from .exceptions import ChangeApprovalRequired


#: True while an outer delete() is already isolated: a delete it triggers itself
#: must not be isolated again, or the outer delete would carry on without it.
_in_isolated_delete = ContextVar('netbox_change_approval.in_isolated_delete', default=False)


class BatchCollector:
    #: The validator raises ChangeApprovalRequired (bulk edit, bulk delete).
    RAISE = 'raise'
    #: The validator parks the exception on the instance instead (bulk import).
    HOLD = 'hold'

    def __init__(self, mode=RAISE):
        self.mode = mode
        self.queued = []   # new ChangeRequests, saved
        self.locked = []   # existing pending requests that blocked an object
        self.applied = 0   # objects written directly (no approval needed)

    def record(self, exc):
        change_request = exc.change_request
        if exc.locked:
            self.locked.append(change_request)
            return
        if change_request.pk is None:
            change_request.save()
        self.queued.append(change_request)


@contextmanager
def collecting(mode=BatchCollector.RAISE):
    collector = BatchCollector(mode)
    token = batch_collector.set(collector)
    try:
        yield collector
    finally:
        batch_collector.reset(token)


def _run_isolated(collector, model, func):
    """
    Run func() in a savepoint. If it raises ChangeApprovalRequired, undo it,
    drop any events it queued, record the request and return (False, None).
    """
    queue = events_queue.get()
    before = dict(queue)
    try:
        with transaction.atomic(using=router.db_for_write(model)):
            return True, func()
    except ChangeApprovalRequired as exc:
        queue.clear()
        queue.update(before)
        collector.record(exc)
        return False, None


def _wrap_update_objects(original):
    @wraps(original)
    def _update_objects(self, form, request, *args, **kwargs):
        collector = batch_collector.get()
        model = self.queryset.model
        if collector is None or not is_gated_model(model):
            return original(self, form, request, *args, **kwargs)

        selected = form.cleaned_data['pk']
        updated = []
        try:
            for item in list(selected):
                # Same type NetBox put there: a queryset (pk__in accepts it).
                form.cleaned_data['pk'] = model.objects.filter(pk=getattr(item, 'pk', item))
                ok, result = _run_isolated(
                    collector, model, lambda: original(self, form, request, *args, **kwargs),
                )
                if ok:
                    updated.extend(result)
                    collector.applied += len(result)
        finally:
            form.cleaned_data['pk'] = selected
        return updated

    _update_objects._nbca_wrapped = True
    return _update_objects


def _wrap_save_object(original):
    @wraps(original)
    def _save_object(self, *args, **kwargs):
        collector = batch_collector.get()
        if collector is None:
            return original(self, *args, **kwargs)

        # NetBox 4.2: (import_form, model_form, request); later: (model_form, request, idx)
        model_form = next(
            (arg for arg in args if hasattr(arg, 'instance') and hasattr(arg, 'save')), None,
        )
        held = getattr(getattr(model_form, 'instance', None), '_nbca_held', None)
        if held is not None:
            del model_form.instance._nbca_held
            collector.record(held)
            return model_form.instance

        result = original(self, *args, **kwargs)
        collector.applied += 1
        return result

    _save_object._nbca_wrapped = True
    return _save_object


def wrap_model_delete(model):
    """Make `model.delete()` run in its own savepoint while a batch is collecting."""
    original = model.delete
    if getattr(original, '_nbca_wrapped', False):
        return

    @wraps(original)
    def delete(self, *args, **kwargs):
        collector = batch_collector.get()
        if collector is None or _in_isolated_delete.get():
            return original(self, *args, **kwargs)
        token = _in_isolated_delete.set(True)
        try:
            ok, result = _run_isolated(collector, type(self), lambda: original(self, *args, **kwargs))
        finally:
            _in_isolated_delete.reset(token)
        if ok:
            collector.applied += 1
            return result
        return 0, {}

    delete._nbca_wrapped = True
    model.delete = delete


def _wrap_pre_save_operations(original):
    """
    Bulk edit's "Add ..." / "Remove ..." fields (tags on every model; tagged
    VLANs, contact groups, module bay types on some) are applied by NetBox
    after the object is saved, in post_save_operations(), so the validator
    never saw them and they needed no approval. Put the resulting set in
    `_m2m_values` before validation instead, so it's part of the diff: queued
    like any other change, or saved as usual when no approval is needed.
    NetBox's own post_save_operations() still does the actual write.
    """
    @wraps(original)
    def pre_save_operations(self, form, obj):
        original(self, form, obj)
        if is_gated_model(type(obj)):
            for field in obj._meta.local_many_to_many:
                _fold_add_remove(form, obj, field.name)

    pre_save_operations._nbca_wrapped = True
    return pre_save_operations


def _fold_add_remove(form, obj, name):
    add = form.cleaned_data.get(f'add_{name}') or []
    remove = form.cleaned_data.get(f'remove_{name}') or []
    if not add and not remove:
        return
    if name == 'tagged_vlans' and getattr(obj, 'mode', None) != 'tagged':
        return  # NetBox only applies tagged VLANs to interfaces in tagged mode
    m2m_values = getattr(obj, '_m2m_values', None)
    if m2m_values is None:
        m2m_values = obj._m2m_values = {}
    current = m2m_values.get(name)
    if current is None:
        current = list(getattr(obj, name).all()) if obj.pk else []
    remove_pks = {getattr(o, 'pk', o) for o in remove}
    result = {getattr(o, 'pk', o): o for o in current if getattr(o, 'pk', o) not in remove_pks}
    for o in add:
        pk = getattr(o, 'pk', o)
        if pk not in remove_pks:
            result[pk] = o
    m2m_values[name] = list(result.values())


def patch_bulk_views():
    from netbox.views.generic import BulkEditView, BulkImportView

    if not getattr(BulkEditView.pre_save_operations, '_nbca_wrapped', False):
        BulkEditView.pre_save_operations = _wrap_pre_save_operations(BulkEditView.pre_save_operations)

    if not getattr(BulkEditView._update_objects, '_nbca_wrapped', False):
        BulkEditView._update_objects = _wrap_update_objects(BulkEditView._update_objects)
    if not getattr(BulkImportView._save_object, '_nbca_wrapped', False):
        BulkImportView._save_object = _wrap_save_object(BulkImportView._save_object)
