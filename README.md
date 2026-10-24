# netbox-change-approval

Require sign-off from an approval group before changes to NetBox objects take
effect — without NetBox's branching/merge machinery, and without touching every
model in your deployment one by one.

**The flow:** someone edits, creates, or deletes a gated object in the NetBox
UI. **Updates and deletes** are captured and queued instead of being written —
the object stays exactly as it was — the object shows a "pending change"
banner — a member of the right approver group reviews the diff and approves or
rejects it — only on approval does the write actually happen. **Creates** work
differently on purpose: the new object is written immediately, so a second
person browsing the list doesn't submit a duplicate while the first is still
waiting, but it's marked as pending (yellow outline in lists, banner on the
object) and locked against further edits until a member of the approver group
reviews it. Approving keeps it; rejecting deletes the object.

## Why this exists

NetBox already has a powerful branching plugin for staged, mergeable changes
across many objects at once. This plugin is for a simpler, more common need:
*every single edit to certain objects needs a second pair of eyes before it's
live*, with as little ceremony as possible. No branches, no merges — just an
approval queue.

## Requirements

- NetBox **4.7** or later. Tested with NetBox 4.7.3 only; NetBox refuses to
  load the plugin on older versions, which lack hooks it relies on.
- No other NetBox plugins required. This plugin only uses NetBox core (its
  permission system, Event Rules, custom validators) and Django REST Framework,
  both already part of NetBox itself.

## What gets gated

Every model that inherits from NetBox's `NetBoxModel` base class is gated
automatically — every core app (DCIM, IPAM, Virtualization, Circuits, Tenancy,
VPN, Wireless, ...) and, importantly, **any other plugin's models too**, as long
as they use the standard NetBox model base. You don't maintain a list of models
by hand. See "Approval rules" below to skip or auto-approve specific models
or objects.

**Not gated:** the plugin's own models (an approval rule doesn't require its
own approval).

**The REST API is gated too.** NetBox validates API writes with the same
model checks as the web UI, so the same rules apply:

| Request | Needs approval | Response |
| --- | --- | --- |
| Create (`POST`) | yes | `201 Created`, the object exists and is marked pending, like a UI create |
| Update (`PATCH`/`PUT` on an object) | yes | `202 Accepted` with the change request; nothing changed yet |
| Delete (`DELETE` on an object) | yes | `202 Accepted` with the change request; nothing deleted yet |
| Any write | object already has a pending change | `409 Conflict` naming that request |
| Bulk update/delete (list endpoint) | any object needs approval | `400` with a per-object error; nothing changed or queued |

The `202`/`409` body looks like
`{"detail": "...", "change_request": {"id": 12, "action": "update", "status": "pending", "object_id": 34, "display_url": "...", "approver_group": "Change Approvers"}}`.
Bulk API requests are all-or-nothing in NetBox, so objects that need approval
must be sent one at a time. Users with the bypass permission get NetBox's
normal responses. GraphQL is read-only.

Tags and other many-to-many fields are part of the change like any other
field, including bulk edit's "Add tags" / "Remove tags" (and the other
"Add ..." / "Remove ..." fields, such as tagged VLANs or contact groups).

## Installation

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
[docs/configuration.md](docs/configuration.md#changing-settings-in-netbox).

Then, from your NetBox install:

```bash
python manage.py migrate netbox_change_approval
```

Create the `Change Approvers` group (or whatever name you configured) under
**Admin → Groups** and add your approvers to it as members.

### Upgrading

`--force-reinstall --no-deps` makes pip fetch the latest code even if it
thinks the installed version is current; then apply any new database changes
and restart NetBox and its worker:

```bash
pip install --upgrade --force-reinstall --no-deps git+https://github.com/mattivaisanen/netbox-change-approval.git
cd /opt/netbox/netbox && python manage.py migrate
sudo systemctl restart netbox netbox-rq
```

**Admin → System → Plugins** shows the installed version; compare it with
`CHANGELOG.md`.

### With netbox-branching

Approval covers main only. Edits inside a branch aren't gated (nothing
reaches main until the branch is merged), and merging, reverting or syncing
a branch is applied as is: control who may merge with netbox-branching's
own permissions. This plugin's rules and change requests always stay in
main. On netbox-branching releases without plugin resolvers, also add
`'netbox_change_approval.*'` to netbox-branching's `exempt_models` setting.

### Uninstalling

Removing the plugin doesn't break NetBox by itself, but leftovers do: the
Notifications, Bookmarks, Subscriptions, Changelog and Journal Entries pages
(and their API lists) give a server error once they hold a row about one of
the plugin's objects, as does the page of a tag or custom field used on
them, and a dashboard with the Pending Approvals widget falls back to the
default one. `prepare_uninstall` deletes those rows, takes the plugin's
object types off custom fields, custom links, export templates, saved
filters, tags, event rules and permissions (deleting the ones only about
change approval), and removes the widget. Clear them first, while the
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

## Using it

### As an editor

Nothing changes about how you fill out the form.

**Editing or deleting** an existing gated object: submit as usual. If the
change needs approval you'll see a green confirmation naming who it's routed
to (*"Approval sent to: alice, bob (Change Approvers)"*), and the object's
fields (or existence, for a delete) stay exactly as they were. Open the object
again and you'll see a banner at the top linking to the pending request.
**The object is locked** — no one can submit another change against it — until
that request is approved or rejected.

**Creating** a new gated object: submit as usual — the object is created for
real right away, so you (and everyone else) see it immediately in its normal
list view, outlined in yellow as pending. This is deliberate: it's there
specifically so a second person browsing Racks/Devices/IPs/etc. sees yours
already exists instead of creating a duplicate. The object is locked against
further edits until decided. If approved, it simply stays; if rejected, it is
deleted. Pending objects are marked by what's pending: green for new, blue
for an edit (changed values shown as old → new), red and struck through for a
delete, each with a label linking to the request. A pending Device gets the
same color in its rack's elevation view. (`pending_row_style: 'outline'`
brings back the older single yellow outline.)

Bulk edit, bulk import, and bulk delete are each gated as a single request
covering the whole action — approving or rejecting applies to everything in that
one bulk action together, not object-by-object.

Changed your mind before anyone's decided? Open your request (from **My
Requests**, or the banner on the object itself) and click **Retract**, then
pick **Retract and edit** (opens the form with what you submitted; saving it
resubmits the same request) or **Retract and discard** (drops it). No comment
needed. For a create, the object is removed either way, the same as a
rejection; for an update or delete, nothing was written yet. Only you (the
submitter) can retract your own request, and only while it's still pending.

**A new device's interfaces belong to its request.** While a new device or
VM waits for approval, its interfaces, ports, bays, modules, inventory items,
VM disks, and IP/MAC addresses on its interfaces are part of that request.
Whoever submitted the device can add, change and remove them freely (each
page save counts as one update of the device's request, so approvals start
over and approvers are notified again); everyone else is blocked until it's
decided. They show as new in lists, and the request page lists them under
"Included with it". Approving the device approves them; rejecting it removes
them. **Cables follow the device too:** a new cable with an end on the
pending device (say, from its port to an existing patch panel) joins the
device's request, so racking and patching a device is one decision, and the
request page counts the cables. Moving an existing interface, IP, cable or
other object onto a pending device (or off one) still needs its own approval.
Changes to these parts are labelled in NetBox's change log ("Part of change
request #12, waiting for approval"), so nobody mistakes them for approved
changes. `group_components_with_parent: False` turns this off.

**Want to change your request while it's waiting?** Open the object and click
**Update request** (on the yellow banner or the request page), or just use the
object's Edit button. The edit page opens with what you asked for, and its
Save button reads **Update request**. Saving replaces what the request asks
for, makes it the next revision and sends it to the approvers again; any
approvals so far start over. If your edit puts everything back the way it is
now, the request is withdrawn. Only you can do this; for everyone else the
object stays locked. Pending deletes can't be updated: retract them instead.
Only a save from that edit page updates the request; other ways of saving the
object (bulk edit, import, the REST API, another object's form that also saves
it) still treat it as locked.

**Files and images** (a device type's front image, say) uploaded in an edit
that waits for approval are stored right away, so approving the edit links
the object to them. If the request is rejected, withdrawn or expires, or you
upload a different file with **Update request**, the unused file is deleted.

**Sent back for revision?** An approver can send a request back with a note
instead of rejecting it. Open it (from the **Approval Inbox**) and click
**Revise and resubmit**: it opens the same NetBox add/edit page you used,
filled in with what you submitted. Fix it and save, and the same request goes
back to the approvers as its next revision, with their earlier note kept on
it. Or click **Discard** to drop it. For a new object, sending it back removes
the object until you resubmit.

**Approval Inbox:** the button at the top of the left-hand menu, under the
NetBox logo, shows how many requests are waiting on you and opens a page
listing them: requests you need to decide, your requests sent back to you, and
your rejected requests you haven't opened yet. Your own requests still waiting
on others, and your approved ones, are listed there too but aren't counted.
Each request is one compact line: the request, its object type, who it's
from (or who it's waiting on, or who decided it) and how long ago, with the
age in one unit ("10 hours"; hover for the exact time). Click anywhere on a
row to open a quick view of the field changes without leaving the page.
A bulk edit, import, delete or change set shows as one item. Each section
shows 50 items per page, with Previous/Next links. **All change requests**
at the top opens the full list. If field work is on, the inbox also has a
**Field work** section (see "Field work" below). (This page replaced the old
**My Requests** page; old links to it open the inbox.)

### Change sets

Some jobs touch many objects: a new switch, its cables, its IP addresses and
a VM. **Change Approvals → Change Sets** lets you send them for approval as
one item instead of one request each:

1. **Start a change set** with a short name ("Install sw1 in rack A3") and,
   optionally, a ticket number.
2. Make your changes as usual. While the change set is active, a banner on
   every page reminds you, and every change of yours that needs approval
   goes into the set and waits there.
3. Open the change set (**Review & submit** on the banner) and click
   **Submit for approval**.

Approvers see the whole set as one item in their inbox, get one notification,
and decide it together (**Approve all**, **Send back**, **Reject all**).
Until you submit, the set is a draft: approvers don't see it and can't decide
it. **Leave** stops adding to the set without submitting it (**Continue
working in it** picks it up again later); **Discard** withdraws every change
in it. `change_sets: False` turns change sets off.

### As an approver

Go to **Change Approvals → Approvals** (its own top-level nav tab, not under
Plugins) — the actionable queue: pending requests routed to a group you're in
(or to a group of someone you're standing in for, see "Backup approvers"
below).
A red badge next to the page title shows how many are waiting on you right
now. The **Approval Inbox** shows what you've submitted yourself; **All Change
Requests** is the full history (scoped the same way as everywhere else — see
"Who can see which requests" below).

There's also a **"Pending Approvals" dashboard widget** — add it from your
NetBox homepage (**Add Widget → Pending Approvals**). Unlike pointing NetBox's
generic Object List widget at Change Request (which works, but shows every
request unfiltered with no way to act on them), this one shows only what's
routed to you and still pending, with Approve/Reject buttons right there —
decide without leaving the homepage. Like the inbox, it shows one compact
line per request, and clicking a row opens a quick view of its changes.

Each entry shows the object, the action (Create/Update/Delete, color-coded
green/blue/red in the list — matching NetBox's own change-log convention),
who submitted it, and a field-by-field old/new
diff — only fields that actually changed (or, for a new object, fields that
were actually set to something other than their default) are shown, and
anything that references another object (site, rack, location, tenant, ...) is
shown by name, not a bare database ID. Approve applies the change immediately;
reject requires a short comment and discards it permanently. **Send back**
also requires a comment, but returns the request to its submitter to fix and
resubmit, as the same request.

**The change request page** is laid out for deciding. Its title names the
request and the object. A line under it shows the object's site and rack.
One **Changes** table lists each field as it is now and as requested, with
"(empty)" for a blank value. Details and comments sit on the side, and only
the buttons that act on the request are shown. A device request that puts a
device in a rack, takes it out or moves it also shows the rack elevation, so
you see the fit and the neighbours; for a move, the requested spot has a
dashed outline.

**Approvals never overwrite newer edits.** If any field in an update request
was changed after it was submitted (through the API, a script, or a user with
`bypass_approval`), the request can't be approved: its page shows which fields
changed, Approve is hidden, and approving from the queue is refused with an
error. Reject it and resubmit against the current values. The same applies if
the object was deleted meanwhile. Approving a bulk batch is all-or-nothing: one
conflict or validation error means none of the batch is applied.

You can only approve/reject requests routed to a group you belong to (or any
request, if you're a superuser).

**Who can see which requests:** every logged-in user has the **Approval
Inbox** (your own submissions) and **All Change Requests**. A request shows the
object's field values, so it follows NetBox's own view permissions: you see
requests about object types you're allowed to view (e.g. no VPN permission,
no VPN requests), plus your own submissions and anything routed to your
approver groups. Opening a single request also respects object-level
permission constraints. **Approvals** only shows requests routed to a group
you're in. Only the approver group (or a superuser) can approve or reject,
and only the submitter can retract. Nobody can approve, reject or send back
their own request, superusers included: someone else has to sign it off.

### Several approvers

A rule with **Approvals needed** above 1 keeps the request pending until that
many different people from the approver group have approved it. Its page shows
"1 of 2" and who has approved so far. Once you've approved, it leaves your
Approvals list and inbox; rejecting or sending back still works at any point
and ends it for everyone. The submitter never counts, so the group needs at
least one more member than approvals needed (the rule form checks this).
Resubmitting after a send-back starts the count over.

### Backup approvers

Going on holiday? Open **Change Approvals → Away / Stand-in**, pick a
stand-in, and enter the first and last day you're away (leave the last day
empty to stay away until you press **I'm back**). While you're away, your
stand-in can decide requests routed to your approver groups as if they were
a member: the requests appear in their Approvals list, inbox and dashboard
widget, and they get the "needs approval" notifications instead of you. The
same page shows whom you're standing in for. Nobody can decide their own
request, stand-in or not.

### Field work

Approved changes often need someone on site: mounting a device, patching a
cable, configuring an IP address. The plugin turns these into plain tasks,
such as "Mount sw1 in rack A3, U20–21, front" or "Patch sw1 eth1 → pp1 port
3". Tasks cover racking, unracking and moving devices, adding and removing
cables, inserting and removing modules, configuring, moving and removing IP
addresses, and creating and removing VMs. A new device's cables and IP
addresses added while it waited get their own tasks.

A task appears once its request is approved, in the **Field work** section of
the Approval Inbox (columns Task, Request, Location, Approved). Tick a task
off when it's done; that records who and when. **Work sheet by rack** opens
a printable page grouped by site and rack, in black and white with boxes to
tick. Rack and site pages show how many tasks are still open there.
`work_sheets: False` turns this off.

### Comments

Every request page has a **Comments** box, for questions and answers that
don't decide anything ("Why this VLAN?"). Anyone who can open the request can
comment. The submitter, the approver group and earlier commenters get a
notification.

### Expiry

A request nobody decides within its rule's **Expire after (days)** (or the
Default rule's) expires: nothing is applied, a pending
new object is removed, and the object is unlocked. The submitter sees it in
their inbox and gets a notification. NetBox's background worker checks every
hour (the same worker that runs scripts); `manage.py expire_change_requests`
does the same on demand.

### Letting specific users skip approval entirely

This reuses NetBox's own permission system — there's no separate screen for it.

Go to **Admin → Permissions → Add**:
1. Pick the object type(s) this applies to (e.g. DCIM > Interface).
2. Under **Additional actions**, type `bypass_approval`.
3. Assign it to the user or group you want to exempt.
4. Optionally, use the existing **Constraints** field to scope it — e.g.
   `{"tenant__slug": "acme"}` to only bypass approval for one tenant's objects.

Anyone with this permission for a given object type has their changes to that
type applied immediately, same as if no approval gate existed.

**Superusers always have it**: NetBox gives superusers every permission, so
their changes are never queued. To have an admin's changes reviewed, give
them a regular account with the permissions they need instead.

### Approval rules

Everything about *which* changes need approval, from *whom*, lives on one
page: **Change Approvals → Approval Rules**. Only superusers can add, change
or delete rules, whatever NetBox permissions say: a rule can switch approval
off, so editing rules is as powerful as the bypass permission. Anyone with
NetBox's "view" permission on approval rules can read them. Each rule has:

- **Object type**: leave blank to match anything, or pick a model.
- **Constraint**: an optional filter in the same JSON format as NetBox
  permission constraints, e.g. `{"tenant__slug": "acme"}`. A list means "any
  of these". Leave blank to match every object of that type.
- **Action**: Create, Update, Delete, or Any.
- **Outcome**:
  - **Needs approval**: from the chosen **Approver group**, or the Default
    rule's if left blank. **Approvals needed** says how many
    different people from that group must approve (default 1); the change is
    applied when the last one does. **Expire after (days)** closes requests
    nobody decided in time (blank: the Default rule's; 0: never).
  - **Reason required** (with "Needs approval"): the change is refused
    until NetBox's changelog message box is filled in; that text is shown to
    approvers as the request's reason.
  - **Approve automatically**: the change goes through right away and is
    logged as an auto-approved request.
  - **Skip approval**: the change goes through with no record at all.
- **Priority**: rules are checked lowest number first, and **the first enabled
  rule that matches decides**.

The **Default rule** is always the last row. It decides every change no
other rule matches: it always needs approval, and you set its approver
group, approvals needed, reason required and expiry like any rule's. It
can't be deleted or disabled. Upgrading creates it from the
`default_approver_group` and `expire_pending_after_days` values in effect.

For an **update**, "Approve automatically" and "Skip approval" rules must
match both the object as it is now *and* the new values. That's what keeps
an object from being moved out of a rule's scope without approval.

**Example: create and edit "planned" devices freely, but approve going
live.** Object type **DCIM > Device**, constraint `{"status": "planned"}`,
action **Any**, outcome **Approve automatically**. Creating or editing a
planned device goes through; changing it to active needs approval. (Not every
model has a `planned` status, e.g. Prefix and VLAN don't, so add one rule per
model.) With action **Any**, deleting a planned device also goes through; use
two rules (Create and Update) if deletes should still be approved.

**Example: no approval for cables in the Tampere region.** Object type
**DCIM > Cable**, outcome **Skip approval**, constraint:

```json
[
  {"a_terminations__device__site__region__slug": "tampere"},
  {"b_terminations__device__site__region__slug": "tampere"}
]
```

Constraints can follow relations and many-valued properties like a cable's
ends. For objects with a direct site, `{"site__region__slug": "tampere"}`
is enough.

**Upgrading:** earlier versions had three separate pages (Approval Routing
Rules, Auto-Approval Rules, Exempt Models). Migration 0005 copies all of them
into Approval Rules, keeping the order the old code checked them in. Newer
versions remove the old tables and their change log entries (migrations 0008
and 0009).

### Turning the plugin off for a model entirely

`exempt_models` in `PLUGINS_CONFIG` (`configuration.py`) is a list of
`app_label.model_name` strings, e.g. `['dcim.cable']`. Listed models aren't
touched by the plugin at all. It needs a NetBox restart, so for day-to-day
use prefer a **Skip approval** rule; this setting is an emergency off switch.
Because it is the emergency off switch, only `configuration.py` sets it: the
settings page shows it but can't change it.

### Activity log

Each action shows up once in NetBox's activity (changelog) list. Submitting a
new object, approving, or rejecting a new object logs the object itself, with
the Message column saying what happened, e.g. "Waiting for approval (change
request #12)" or "Approved by anna (change request #12)". The change request
gets its own entry only when nothing else was written, such as an edit or
delete that is still waiting, or approving a new object. A message the user
typed is kept, with ours added after it. Changes to the parts of a pending
new device (its interfaces, cables, IP addresses, ...) say "Part of change
request #12, waiting for approval".

### Getting notified of pending changes

Only the people a request concerns hear about it: its submitter and its
approver group (a stand-in instead of an away approver). NetBox's own
notifications (the bell in the top bar) work out of the box:

| Event | Who | Example |
|---|---|---|
| New or resubmitted request | approver group, except the submitter | `#12 Update device sw1 · from alice` |
| Approved, but needs more approvers | submitter | `… · approved by bob (1 of 2)` |
| Approved | submitter | `… · approved by bob` |
| Rejected | submitter | `… · rejected by bob: wrong rack` |
| Sent back for revision | submitter | `… · sent back by bob: use U20` |
| Expired | submitter | `… · nobody decided it in time` |
| New comment | submitter and approver group, except the author | `… · bob: why this VLAN?` |

Each notification in the bell also says who did it ("By: bob"), and the
Notifications page has a "By" column. This covers NetBox's own notifications
too (an Event Rule's or a subscription's "Object updated", say): those name
the user of the change behind them.

Once a request is decided, approvers' unread "needs approval" notifications
for it disappear. A bulk action or a submitted change set is one
notification.

**Email, chat or anything else:** each notification also fires a NetBox
event of the same type (they are listed as event types like "Change request
approved" when you add an Event Rule). Its data has everything a message
needs, including who should get it:

- `display`, `detail`, `url`: the notification text and the request's link
  (prefix your NetBox address).
- `action`, `status`, `revision`, `object_type`, `object`, `changes`
  (`[{field, old, new}]`).
- `submitted_by`, `by` (who did it), `approvers`, `recipients`: each
  `{username, name, email}`.
- `comment`, `approver_group`, `approvals_needed`.

**Setup guide:** [docs/webhooks.md](docs/webhooks.md) shows the Event
Rule and webhook, every data field, and step-by-step Power Automate examples
for an Outlook e-mail to the recipients and a Teams message to each person
from the Flow bot.

A Notification action with a notification group also works, but sends to
that fixed group rather than to each request's own people.

## Limitations (current version)

- **No bulk approval requests through the API.** A bulk API update or delete
  that touches an object needing approval is refused for that object (see
  the table above), rather than queued.
- **Constraints on brand-new objects are best-effort.** A routing/auto-approval
  rule's constraint can only be checked against the database for objects that
  already exist. For a pending *create*, there's no database row yet, so
  constraints are checked against whatever's already set in memory on the
  unsaved instance (this covers the common case — e.g. a `tenant` picked in the
  form — but can't follow relations that aren't assigned yet).
- **Management commands bypass gating.** Changes need a logged-in user
  behind them to be gated: the web UI, the REST API, background jobs and
  custom scripts run from NetBox are gated (a script's edits and new objects
  become requests from the user who ran it). `manage.py` commands and
  `nbshell` have no user and are not gated.
- **No retention policy.** Every change request — approved, rejected, auto-
  approved — stays in the database forever; there's no built-in pruning. Also
  worth knowing before years of use: approver groups become permanently
  undeletable once any historical request references them, and uninstalling
  the plugin doesn't clean up permissions, or (unless you reverse-
  migrate) its own tables (see "Uninstalling"). See DEVELOPER.md "Long-term data lifecycle, and
  what happens if you uninstall this" before committing to this long-term.

---

✨ *Vibecoded with pride.* ✨

## License

Apache License 2.0 — see [LICENSE](LICENSE).
