"""
Working alongside netbox-branching (optional; nothing here runs without it).

netbox-branching gives each branch its own copy of the database, and merges
a branch into main by replaying its changes there, as the users who made
them. Without this module the two plugins fight:

- Edits inside a branch were gated, and their change requests were saved in
  the branch's copy of the database, where main's approvers never see them.
- Merging a branch replayed each change through the gate: edits by users
  without the bypass permission were silently undone during the merge, and
  deletes made the merge fail.

So approval covers main only. Inside a branch nothing is gated (the branch
is a draft; nothing reaches main until it's merged), and a merge, revert or
sync is applied as is: it is the branch's own review point, controlled by
netbox-branching's permissions. This plugin's own tables (rules, change
requests) always stay in main.
"""
from contextvars import ContextVar
from functools import wraps

#: True while netbox-branching merges, reverts or syncs a branch.
_branch_operation = ContextVar('netbox_change_approval.branch_operation', default=False)

#: Branch methods that replay changes onto a schema.
_REPLAYING_METHODS = ('merge', 'revert', 'sync')


def outside_main():
    """True when a write must not be gated because netbox-branching is
    handling it: inside a branch, or during a merge, revert or sync."""
    if _branch_operation.get():
        return True
    try:
        from netbox_branching.contextvars import active_branch
    except ImportError:
        return False
    return active_branch.get() is not None


def _own_models_stay_in_main(model):
    """netbox-branching resolver: this plugin's models are never branched."""
    if model._meta.app_label == 'netbox_change_approval':
        return False
    return None


def _mark_replay(method):
    if getattr(method, '_nbca_wrapped', False):
        return method

    @wraps(method)
    def wrapper(*args, **kwargs):
        token = _branch_operation.set(True)
        try:
            return method(*args, **kwargs)
        finally:
            _branch_operation.reset(token)

    wrapper._nbca_wrapped = True
    return wrapper


def connect():
    """Called from ready(). Does nothing if netbox-branching isn't installed."""
    try:
        from netbox_branching.models import Branch
    except ImportError:
        return
    try:
        from netbox_branching.utilities import register_branching_resolver
    except ImportError:  # older netbox-branching: use its exempt_models setting instead (README)
        pass
    else:
        register_branching_resolver(_own_models_stay_in_main)
    for name in _REPLAYING_METHODS:
        method = getattr(Branch, name, None)
        if method is not None:
            setattr(Branch, name, _mark_replay(method))
