# Configuration

All settings live under `PLUGINS_CONFIG['netbox_change_approval']` in your
NetBox `configuration.py`.

| Key | Type | Default | Description |
|---|---|---|---|
| `default_approver_group` | str | `'Change Approvers'` | Name of the NetBox `Group` (`users.Group`) the **Default rule** (bottom of Change Approval → Approval Rules) starts with when it is created, and its fallback while it leaves the approver group blank. Set the group on the Default rule instead. If no such group exists, changes are still queued but only superusers can decide them. |
| `exempt_models` | list[str] | `[]` | Emergency off switch: models the plugin doesn't touch at all, as `'app_label.model_name'` strings (e.g. `'extras.tag'`). Needs a restart; for normal use prefer a "Skip approval" Approval Rule. Applies in addition to this plugin's own models, which are always excluded regardless of this setting. |
| `expire_pending_after_days` | int or None | `None` | Only the starting **Expire after (days)** of the Default rule when it is created. Set expiry on the Default rule instead. |
| `pending_row_style` | str | `'by_action'` | How pending objects look in object lists and rack elevations: `'by_action'` (green new, blue edit, red delete, with the requested values) or `'outline'` (a single yellow outline). |
| `group_components_with_parent` | bool | `True` | While a new device or VM waits for approval, its interfaces, ports, bays, modules, inventory items, VM disks and IP/MAC addresses on its interfaces belong to its request instead of getting their own. `False` gives each its own request again. |
| `work_sheets` | bool | `True` | Field work: approved changes that need work on site (mounting a device, patching a cable, configuring an IP address, ...) become tasks in the **Field work** section of the Approval Inbox, with a printable work sheet by rack. `False` turns it off. |
| `change_sets` | bool | `True` | Change sets (Change Approval → Change Sets): collect several changes and submit them for approval as one item. `False` turns them off. |
| `netbox_url` | str | `''` | NetBox's address as users reach it, e.g. `https://netbox.example.com`. Puts full links (`link`) in webhook data; see [webhooks.md](webhooks.md). Only in `configuration.py`. |

Example:

```python
PLUGINS_CONFIG = {
    'netbox_change_approval': {
        'exempt_models': [
            'extras.tag',
        ],
        'pending_row_style': 'by_action',
        'group_components_with_parent': True,
        'work_sheets': True,
        'change_sets': True,
    },
}
```

## Changing settings in NetBox

Superusers can change `pending_row_style`, `group_components_with_parent`,
`work_sheets` and `change_sets` on
**Change Approval → Settings**, without editing `configuration.py` or
restarting NetBox:

- `configuration.py` (or the plugin default) gives each setting its value.
- A value saved on the page overrides it. The page labels each setting
  *Saved here*, *From configuration.py* or *Default*, and for a saved value
  shows who saved it, when, and what `configuration.py` says.
- **Use configuration.py** next to a saved value removes it, so the setting
  follows `configuration.py` again. Saving a value that means the same as
  `configuration.py`'s also removes the saved value, so only real differences
  are kept.
- Changes take effect at once in every NetBox process and the background
  worker. Saved values are read when used (once per web request), not at
  startup.
- Changing a setting doesn't itself need approval.
- Only superusers see the page (it's in the Change Approval menu for them
  only).

The default approver group, approvals needed, reason required and expiry for
changes no rule matches are not settings: they are the **Default rule** at the
bottom of **Change Approval → Approval Rules**. Upgrading moves the values in
effect (saved on the settings page, else `configuration.py`) into that rule.

`exempt_models` stays `configuration.py`-only. It is the emergency off switch:
it decides at startup which models the plugin touches at all, and it has to
work even when the plugin, its database table or its pages are what's broken.
A value saved in the database must never be able to override it. The settings
page shows it read-only.

## Things configured in the NetBox UI, not here

These are deliberately **not** `configuration.py` settings — they're ordinary
NetBox-managed data so they can change without a restart, and because they
reuse infrastructure NetBox already has a UI for:

- **Bypass permission** — Admin → Permissions (a standard `ObjectPermission`
  with the custom action `bypass_approval`). See README "Letting specific users
  skip approval entirely".
- **Approval rules** — Change Approval → Approval Rules. Who approves what,
  and what is auto-approved or skipped. Rows in the `ApprovalRule` table.
- **Stand-ins** — Change Approval → Away / Stand-in. Each approver names
  their own stand-in and the days they're away. See README "Backup
  approvers".
- **Notifications** — Operations → Event Rules, against the `Change Request`
  object type. See README "Getting notified of pending changes".
