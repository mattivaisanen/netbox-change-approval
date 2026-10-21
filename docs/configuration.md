# Configuration reference

All settings live under `PLUGINS_CONFIG['netbox_change_approval']` in your
NetBox `configuration.py`.

| Key | Type | Default | Description |
|---|---|---|---|
| `default_approver_group` | `str` | `'Change Approvers'` | Name of the NetBox `Group` (`users.Group` — NetBox ships its own, not Django's stock `auth.Group`) used to approve a change when no `ApprovalRoutingRule` matches the object. Must already exist (Admin → Groups) — if it doesn't, changes are still queued but have no approver group assigned, and only superusers can decide them. |
| `exempt_models` | `list[str]` | `[]` | Models to exclude from gating entirely, as `'app_label.model_name'` strings (e.g. `'extras.tag'`). Applies in addition to this plugin's own models, which are always excluded regardless of this setting. |

Example:

```python
PLUGINS_CONFIG = {
    'netbox_change_approval': {
        'default_approver_group': 'Network Change Board',
        'exempt_models': [
            'extras.tag',
        ],
    },
}
```

## Things configured in the NetBox UI, not here

These are deliberately **not** `configuration.py` settings — they're ordinary
NetBox-managed data so they can change without a restart, and because they
reuse infrastructure NetBox already has a UI for:

- **Bypass permission** — Admin → Permissions (a standard `ObjectPermission`
  with the custom action `bypass_approval`). See README "Letting specific users
  skip approval entirely".
- **Approver routing rules** — Plugins → Change Approval → Approval Routing
  Rules. Rows in the `ApprovalRoutingRule` table.
- **Auto-approval rules** — Plugins → Change Approval → Auto-Approval Rules.
  Rows in the `AutoApprovalRule` table.
- **Notifications** — Operations → Event Rules, against the `Change Request`
  object type. See README "Getting notified of pending changes".
