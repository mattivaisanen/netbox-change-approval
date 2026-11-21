"""
Public interface for other plugins and scripts (see docs/integrating.md).

Everything here is stable: it keeps its name and meaning across patch and
minor releases, and a change is announced in CHANGELOG.md. All other modules
of this plugin are internal and may change in any release.

Import only from this module, and guard the import, so your plugin still
works without netbox-change-approval installed:

    try:
        from netbox_change_approval import public as change_approval
    except ImportError:
        change_approval = None

This module only re-exports and wraps internals; it adds no behavior.
"""
import uuid
from contextlib import contextmanager

from .context import batch_scope
from .context import current_batch as _current_batch

__all__ = (
    'ACTION_CREATE',
    'ACTION_DELETE',
    'ACTION_UPDATE',
    'ActionChoices',
    'ChangeApprovalRequired',
    'OPEN_STATUSES',
    'STATUS_APPLIED',
    'STATUS_EXPIRED',
    'STATUS_PENDING',
    'STATUS_REJECTED',
    'STATUS_RETRACTED',
    'STATUS_RETURNED',
    'StatusChoices',
    'batch',
    'current_batch',
    'is_gated',
    'pending_create_for',
    'visible_change_requests',
)

# Plain strings, so this module can be imported before Django's apps are
# loaded (e.g. at the top of another plugin's module). They equal the
# StatusChoices/ActionChoices values (checked by tests/test_public.py).

#: ChangeRequest.status values.
STATUS_PENDING = 'pending'
STATUS_APPLIED = 'applied'
STATUS_REJECTED = 'rejected'
STATUS_RETRACTED = 'retracted'
STATUS_RETURNED = 'returned'
STATUS_EXPIRED = 'expired'

#: Statuses of a request that is not finished yet: waiting for approvers, or
#: sent back to its submitter for changes.
OPEN_STATUSES = (STATUS_PENDING, STATUS_RETURNED)

#: ChangeRequest.action values.
ACTION_CREATE = 'create'
ACTION_UPDATE = 'update'
ACTION_DELETE = 'delete'

#: Re-exported lazily (they need the models loaded): StatusChoices and
#: ActionChoices (TextChoices with labels), and ChangeApprovalRequired, the
#: exception raised when a change was queued for approval instead of written.
_LAZY = {
    'StatusChoices': 'models',
    'ActionChoices': 'models',
    'ChangeApprovalRequired': 'exceptions',
}


def __getattr__(name):
    if name in _LAZY:
        from importlib import import_module

        return getattr(import_module(f'.{_LAZY[name]}', __package__), name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


@contextmanager
def batch(batch_id=None):
    """
    Group every change request created inside the block into one batch, which
    approvers see and decide as one item (as for a bulk edit). Yields the
    batch ID: `batch_id` if given (a UUID), else a new one.

        with change_approval.batch():
            for device in devices:
                device.full_clean()
                device.save()
    """
    batch_id = batch_id or uuid.uuid4()
    with batch_scope(batch_id):
        yield batch_id


def current_batch():
    """The batch ID requests created now are grouped under, or None."""
    return _current_batch.get()


def is_gated(model):
    """Do changes to `model` (a model class or instance) need approval rules
    checked? False for this plugin's own models and for `exempt_models`.
    Whether a given change needs approval still depends on the rules and the
    user's permissions."""
    from .discovery import is_gated_model

    return is_gated_model(model if isinstance(model, type) else type(model))


def pending_create_for(obj):
    """The pending create request of a saved object (added, but not approved
    yet), or None."""
    if obj is None or obj.pk is None:
        return None
    from core.models import ObjectType

    from .models import ChangeRequest

    return ChangeRequest.objects.filter(
        content_type=ObjectType.objects.get_for_model(type(obj)),
        object_id=obj.pk,
        action=ACTION_CREATE,
        status=STATUS_PENDING,
    ).first()


def visible_change_requests(user, queryset=None):
    """
    Change requests `user` may read, as in the web UI and REST API: the user
    may view the object, or submitted the request, or is one of its
    approvers. `queryset` (default: all change requests) is narrowed further;
    it is not checked against the view permission on change requests, so
    pass `ChangeRequest.objects.restrict(user, 'view')` for that.
    """
    from .models import ChangeRequest
    from .permissions import visible_change_requests as _visible

    if queryset is None:
        queryset = ChangeRequest.objects.all()
    return _visible(user, queryset)
