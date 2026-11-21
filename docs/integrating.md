# Integrating with other plugins

Other NetBox plugins and scripts can work with change approval through one
module, `netbox_change_approval.public`. Its names are stable: they keep
their name and meaning across releases, and any change is announced in
[CHANGELOG.md](../CHANGELOG.md). Every other module of the plugin is
internal and may change in any release.

Import it guarded, so your plugin also works without change approval:

```python
try:
    from netbox_change_approval import public as change_approval
except ImportError:
    change_approval = None
```

The module can be imported at the top of your own modules: it doesn't need
Django's apps to be loaded until you call a function.

## What it offers

| Name | What it does |
| --- | --- |
| `batch(batch_id=None)` | Context manager. Change requests created inside it share one batch, which approvers see and decide as one item, like a bulk edit. Yields the batch ID (a new UUID unless you pass one). |
| `current_batch()` | The batch ID requests created now go into, or `None`. |
| `is_gated(model)` | Whether changes to a model (class or instance) go through approval rules at all. `False` for the plugin's own models and for `exempt_models`. Whether a given change needs approval still depends on the rules and the user's permissions. |
| `pending_create_for(obj)` | The pending create request of a saved object, i.e. one that was added but isn't approved yet, or `None`. |
| `visible_change_requests(user, queryset=None)` | The change requests a user may read, by the same rule as the web UI and the REST API: the user may view the object, submitted the request, or is one of its approvers. Narrows `queryset` (default: all change requests). For the view permission on change requests themselves, pass `ChangeRequest.objects.restrict(user, 'view')`. |
| `STATUS_PENDING`, `STATUS_APPLIED`, `STATUS_REJECTED`, `STATUS_RETRACTED`, `STATUS_RETURNED`, `STATUS_EXPIRED` | Values of a change request's `status`. |
| `OPEN_STATUSES` | Statuses of a request that isn't finished: pending, or sent back to its submitter. |
| `ACTION_CREATE`, `ACTION_UPDATE`, `ACTION_DELETE` | Values of a change request's `action`. |
| `StatusChoices`, `ActionChoices` | The same values as Django choices, with labels. |
| `ChangeApprovalRequired` | Raised when a change was queued for approval instead of being written. Its `change_request` attribute is the request. |

## Examples

Group the changes of an import into one approval:

```python
def import_devices(rows):
    if change_approval is None:
        return _import(rows)
    with change_approval.batch():
        return _import(rows)
```

Show that an object is still waiting to be approved:

```python
def awaiting_approval(obj):
    return change_approval is not None and change_approval.pending_create_for(obj) is not None
```

List the pending change requests a user may see:

```python
from django.apps import apps

ChangeRequest = apps.get_model('netbox_change_approval', 'ChangeRequest')
requests = change_approval.visible_change_requests(
    user, ChangeRequest.objects.restrict(user, 'view').filter(status=change_approval.STATUS_PENDING),
)
```

## Deletes from your own signal receivers

If your plugin deletes related rows from its own `pre_delete` receiver, those
deletes follow the delete that started them: when that delete is allowed
(an auto-approval rule, the bypass permission, or an approved change being
applied), yours pass too. When it needs approval, the request is for the
object the user deleted, and nothing is deleted until it is approved.
