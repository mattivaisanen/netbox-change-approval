"""
Process-local (contextvars) flags shared by the gating logic, the delete
signal, and the middleware. Neither is persisted anywhere; each only matters for
the lifetime of a single request/apply call.
"""
from contextlib import contextmanager
from contextvars import ContextVar

#: True while ChangeRequest.apply() is performing the real, approved write. Gates must
#: let this one through unconditionally — otherwise approving a change could never
#: actually be written, since the write would just get re-queued as another pending
#: change forever.
applying_change = ContextVar('netbox_change_approval.applying_change', default=False)

#: Set by ChangeApprovalMiddleware.process_view() for the duration of a bulk edit/
#: import/delete request, to a fresh UUID. Every object touched by that one bulk
#: action gets its own ChangeRequest row, but all of them share this batch_id, so
#: the approval queue can group them and a single reject-with-comment kills the
#: whole batch.
current_batch = ContextVar('netbox_change_approval.current_batch', default=None)


@contextmanager
def applying_change_on():
    token = applying_change.set(True)
    try:
        yield
    finally:
        applying_change.reset(token)


@contextmanager
def batch_scope(batch_id):
    token = current_batch.set(batch_id)
    try:
        yield
    finally:
        current_batch.reset(token)

#: Set (to a bulk.BatchCollector) by ChangeApprovalMiddleware for the duration of a
#: bulk edit/import/delete request, next to current_batch. While set, an object
#: that needs approval is skipped and its request collected, instead of the first
#: such object aborting the whole bulk action. See bulk.py.
batch_collector = ContextVar('netbox_change_approval.batch_collector', default=None)


class _DeleteScope:
    """The outermost delete() of a gated object (`root`); `allowed` is set once
    gate_delete lets it through."""

    def __init__(self, root):
        self.root = root
        self.allowed = False


#: Set (to a _DeleteScope) for the duration of the outermost delete() of a
#: gated object (see bulk.wrap_model_delete). Once gate_delete has let that
#: delete through (auto-approval rule, bypass permission, or an approved change
#: being applied), every delete started while it runs passes too. Such nested
#: deletes come from other receivers, e.g. another plugin's pre_delete calling
#: related.delete(); Django gives them their own `origin`, so they would
#: otherwise be gated on their own and fail the delete that was already allowed.
delete_scope = ContextVar('netbox_change_approval.delete_scope', default=None)


@contextmanager
def delete_scope_open(root):
    """Open a delete scope for `root` unless one is already open (nested
    deletes share the outer one)."""
    if delete_scope.get() is not None:
        yield
        return
    token = delete_scope.set(_DeleteScope(root))
    try:
        yield
    finally:
        delete_scope.reset(token)


def allow_deletes_in_scope():
    """Mark the open delete scope as allowed (no-op when none is open, e.g. for
    QuerySet.delete(), which doesn't go through Model.delete())."""
    scope = delete_scope.get()
    if scope is not None:
        scope.allowed = True


def inside_allowed_delete():
    scope = delete_scope.get()
    return scope is not None and scope.allowed


def delete_scope_root():
    """The object whose delete() opened the current scope, or None."""
    scope = delete_scope.get()
    return None if scope is None else scope.root
