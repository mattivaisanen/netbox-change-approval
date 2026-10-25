# Installation

The plugin isn't on PyPI; install it from GitHub into NetBox's own Python
environment (on a standard install, `source /opt/netbox/venv/bin/activate`
first):

```bash
pip install git+https://github.com/mattivaisanen/netbox-change-approval.git
```

Add it to your NetBox `configuration.py`:

```python
PLUGINS = [
    'netbox_change_approval',
]

PLUGINS_CONFIG = {
    'netbox_change_approval': {
        # Approver group the Default rule (Approval Rules) starts with.
        # Must be an existing NetBox group (Admin -> Groups).
        'default_approver_group': 'Change Approvers',
        # Optional: models to exclude from gating entirely, as 'app_label.model' strings.
        'exempt_models': [],
        # Optional: 'outline' brings back the single yellow outline for pending
        # objects in lists and rack elevations, instead of colors per action.
        'pending_row_style': 'by_action',
        # Optional: False gives each component of a pending new device/VM
        # (interface, port, IP on it, ...) its own request again.
        'group_components_with_parent': True,
        # Optional: False hides the Field work section and work sheet.
        'work_sheets': True,
        # Optional: False turns off change sets.
        'change_sets': True,
    },
}
```

Superusers can also change these settings in NetBox, under **Change
Approvals → Settings** (all except `exempt_models`). A value saved there
overrides `configuration.py` until you press **Use configuration.py** next to
it, and takes effect at once, without a restart. Each setting shows where its
value comes from (*Saved here*, *From configuration.py* or *Default*).
`exempt_models` is shown read-only: it is the emergency off switch and only
`configuration.py` sets it. See
[configuration.md](configuration.md#changing-settings-in-netbox).

Then, from your NetBox install:

```bash
python manage.py migrate netbox_change_approval
```

Create the `Change Approvers` group (or whatever name you configured) under
**Admin → Groups** and add your approvers to it as members.

## Upgrading

`--force-reinstall --no-deps` makes pip fetch the latest code even if it
thinks the installed version is current; then apply any new database changes
and restart NetBox and its worker:

```bash
pip install --upgrade --force-reinstall --no-deps git+https://github.com/mattivaisanen/netbox-change-approval.git
cd /opt/netbox/netbox && python manage.py migrate
sudo systemctl restart netbox netbox-rq
```

**Admin → System → Plugins** shows the installed version; compare it with
[CHANGELOG.md](../CHANGELOG.md).

## With netbox-branching

Approval covers main only. Edits inside a branch aren't gated (nothing
reaches main until the branch is merged), and merging, reverting or syncing
a branch is applied as is: control who may merge with netbox-branching's
own permissions. This plugin's rules and change requests always stay in
main. On netbox-branching releases without plugin resolvers, also add
`'netbox_change_approval.*'` to netbox-branching's `exempt_models` setting.

## Uninstalling

Removing the plugin doesn't break NetBox by itself, but leftovers do: the
Notifications, Bookmarks, Subscriptions, Changelog and Journal Entries pages
(and their API lists) give a server error once they hold a row about one of
the plugin's objects, as does the page of a tag or custom field used on
them, and a dashboard with the Pending Approvals widget falls back to the
default one. `prepare_uninstall` deletes those rows, takes the plugin's
object types off custom fields, custom links, export templates, saved
filters, tags, event rules and permissions (deleting the ones only about
change approval), deletes custom fields whose values are change requests or
rules, takes the plugin's events off event rules, removes the widget and
cancels the plugin's queued background jobs. Clear them first, while the
plugin is still installed:

```bash
cd /opt/netbox/netbox && python manage.py prepare_uninstall
```

It shows what it will remove and asks before doing it (`--dry-run` only
shows). It also warns about change requests that are still pending: decide
them first, because once the plugin is gone pending edits and deletes are
lost and pending new objects stay as normal objects. Change requests and
rules are kept. Then take `netbox_change_approval` out of `PLUGINS` and
`PLUGINS_CONFIG`, restart NetBox and its worker, and uninstall the package.

To also delete all request history and rules, run
`python manage.py migrate netbox_change_approval zero` before taking it out
of `PLUGINS`.
