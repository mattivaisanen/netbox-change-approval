# netbox-change-approval

A NetBox plugin that requires sign-off from an approval group before changes
to NetBox objects take effect. There are no branches to merge: every gated
edit simply waits in an approval queue until someone else approves it.

## How it works

- **Edits and deletes** are queued instead of written. The object stays as it
  was, shows a "Waiting for approval" banner, and is locked until an approver
  decides.
- **New objects** are written at once, so nobody creates a duplicate, but
  they are marked as pending in lists and rack elevations and locked until
  decided. Rejecting a new object deletes it.
- **Approvers** see the requested changes field by field (current value and
  requested value) and approve, reject or send the request back for revision.
  Nobody can approve their own request.

Every model built on NetBox's `NetBoxModel` is gated, including other plugins'
models, in the web UI, the REST API, background jobs and custom scripts.

## Features

- Approval rules: pick the approver group, number of approvals, required
  reason and expiry per object type, action or constraint, or let matching
  changes through automatically.
- Approval Inbox, Approvals queue and a Pending Approvals dashboard widget;
  the widget and the inbox count update live.
- Change sets: collect many changes (a new switch, its cables and IPs) and
  submit them as one request.
- A new device's interfaces, ports, IP addresses and cables belong to its
  request and are decided with it.
- Update, retract or resubmit your own request while it waits.
- Stand-ins for approvers who are away.
- Field work: approved changes become printable on-site tasks ("Mount sw1 in
  rack A3, U20-21").
- Comments, expiry, NetBox notifications, and NetBox events for e-mail, Teams
  or webhooks.
- A `bypass_approval` permission for trusted users, using NetBox's own
  permission system.

## Requirements

- NetBox 4.7 or later (tested with 4.7.3). NetBox refuses to load the plugin
  on older versions.
- No other plugins. Works alongside netbox-branching; see
  [docs/installation.md](docs/installation.md#with-netbox-branching).

## Installation

Install into NetBox's Python environment:

```bash
pip install git+https://github.com/mattivaisanen/netbox-change-approval.git
```

Enable it in `configuration.py`:

```python
PLUGINS = ['netbox_change_approval']

PLUGINS_CONFIG = {
    'netbox_change_approval': {
        # An existing NetBox group (Admin > Groups) whose members approve.
        'default_approver_group': 'Change Approvers',
    },
}
```

Apply the database changes and restart NetBox and its worker:

```bash
cd /opt/netbox/netbox && python manage.py migrate netbox_change_approval
```

Then create the approver group under **Admin > Groups** and add your
approvers to it. Upgrading and uninstalling are covered in
[docs/installation.md](docs/installation.md).

## Documentation

- [Installation, upgrading and uninstalling](docs/installation.md)
- [Using the plugin](docs/usage.md): editors, approvers, approval rules,
  change sets, notifications and limitations
- [Configuration reference](docs/configuration.md)
- [Notifications by e-mail, Teams or webhooks](docs/webhooks.md)
- [Developer notes](DEVELOPER.md)
- [Changelog](CHANGELOG.md)

## License

Apache License 2.0, see [LICENSE](LICENSE).
