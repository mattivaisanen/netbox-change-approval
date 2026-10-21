"""
Two process-local (contextvars) flags shared by the gating logic, the delete
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
