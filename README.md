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
waiting, but it's tagged **"Pending Approval"** and locked against further
edits until a member of the approver group reviews it. Approving just removes
the tag; rejecting deletes the object.

## Why this exists

NetBox already has a powerful branching plugin for staged, mergeable changes
across many objects at once. This plugin is for a simpler, more common need:
*every single edit to certain objects needs a second pair of eyes before it's
live*, with as little ceremony as possible. No branches, no merges — just an
approval queue.

## Requirements

- NetBox **4.2** or later.
- No other NetBox plugins required. This plugin only uses NetBox core (its
  permission system, Event Rules, custom validators) and Django REST Framework,
  both already part of NetBox itself.

## What gets gated

Every model that inherits from NetBox's `NetBoxModel` base class is gated
automatically — every core app (DCIM, IPAM, Virtualization, Circuits, Tenancy,
VPN, Wireless, ...) and, importantly, **any other plugin's models too**, as long
as they use the standard NetBox model base. You don't maintain a list of models
by hand. See "Choosing which object types are gated" below if you want to
exclude specific models — either a config-file list or a live, UI-editable one.

**Not yet gated (see Limitations):** the plugin's own models (an approval rule
doesn't require its own approval), tag assignment, and anything done through the
REST/GraphQL API rather than the web UI.

## Installation

```bash
pip install netbox-change-approval
```

Add it to your NetBox `configuration.py`:

```python
PLUGINS = [
    'netbox_change_approval',
]

PLUGINS_CONFIG = {
    'netbox_change_approval': {
        # Fallback approver group used when no ApprovalRoutingRule matches.
        # Must be an existing Django Group (Admin -> Groups).
        'default_approver_group': 'Change Approvers',
        # Optional: models to exclude from gating entirely, as 'app_label.model' strings.
        'exempt_models': [],
    },
}
```

Then, from your NetBox install:

```bash
python manage.py migrate netbox_change_approval
```

Create the `Change Approvers` group (or whatever name you configured) under
**Admin → Groups** and add your approvers to it as members.

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
list view, with a **"Pending Approval"** tag on it. This is deliberate: it's
there specifically so a second person browsing Racks/Devices/IPs/etc. sees
yours already exists instead of creating a duplicate. The object is locked
against further edits until decided. If approved, the tag is simply removed.
If rejected, the object is deleted. Any list view also outlines a pending
row in yellow (even without the Tags column visible), and a pending Device
gets the same outline in its rack's elevation view.

Bulk edit, bulk import, and bulk delete are each gated as a single request
covering the whole action — approving or rejecting applies to everything in that
one bulk action together, not object-by-object.

Changed your mind before anyone's decided? Open your request (from **My
Requests**, or the banner/tag on the object itself) and click **Retract** — no
comment needed, since there's no one else to explain it to. For a create, this
deletes the object the same way a rejection would; for an update or delete,
nothing was written yet, so it just closes the request. Only you (the
submitter) can retract your own request, and only while it's still pending.

### As an approver

Go to **Change Approvals → Approvals** (its own top-level nav tab, not under
Plugins) — the actionable queue: pending requests routed to a group you're in.
A red badge next to the page title shows how many are waiting on you right
now. **My Requests** shows what you've submitted yourself; **All Change
Requests** is the full history (scoped the same way as everywhere else — see
"Who can see which requests" below).

There's also a **"Pending Approvals" dashboard widget** — add it from your
NetBox homepage (**Add Widget → Pending Approvals**). Unlike pointing NetBox's
generic Object List widget at Change Request (which works, but shows every
request unfiltered with no way to act on them), this one shows only what's
routed to you and still pending, with Approve/Reject buttons right there —
decide without leaving the homepage.

Each entry shows the object, the action (Created/Updated/Deleted, color-coded
green/blue/red in the list — matching NetBox's own change-log convention),
who submitted it, and a field-by-field old/new
diff — only fields that actually changed (or, for a new object, fields that
were actually set to something other than their default) are shown, and
anything that references another object (site, rack, location, tenant, ...) is
shown by name, not a bare database ID. Approve applies the change immediately;
reject requires a short comment and discards it permanently (there is
currently no "send back for revision" — see Limitations).

**Approvals never overwrite newer edits.** If any field in an update request
was changed after it was submitted (through the API, a script, or a user with
`bypass_approval`), the request can't be approved: its page shows which fields
changed, Approve is hidden, and approving from the queue is refused with an
error. Reject it and resubmit against the current values. The same applies if
the object was deleted meanwhile. Approving a bulk batch is all-or-nothing: one
conflict or validation error means none of the batch is applied.

You can only approve/reject requests routed to a group you belong to (or any
request, if you're a superuser).

**Who can see which requests:** any logged-in user can open a single request
(e.g. from the "View request" button on the object) and read its changes.
The lists are scoped: **My Requests** only ever shows your own submissions,
**Approvals** only shows requests routed to a group you're in, and **All
Change Requests** shows the union of both (not every request in the system). To let someone (e.g. a change-management admin) see
every request regardless of who submitted or approves it, grant them the
`view_all` action the same way as `bypass_approval` below, but on object type
**Change Approval > Change
Request**. Superusers always see everything.

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

### Routing different objects to different approver groups

By default every gated object routes to the single `default_approver_group`.
To split this out — e.g. a networking team approves IPAM changes, a different
team approves everything else — go to **Change Approvals → Approval
Routing Rules → Add**:

- **Object type**: leave blank to match anything, or pick a specific model.
- **Constraint**: a Django ORM filter (the exact same JSON format NetBox's own
  object permissions use), e.g. `{"tenant__slug": "acme"}` or
  `{"site__region__slug": "emea"}`. Leave blank to match unconditionally.
- **Priority**: rules are evaluated in ascending order; the first enabled rule
  that matches wins.
- **Approver group**: who gets routed this change.

If no rule matches, the plugin falls back to `default_approver_group`.

### Auto-approving certain changes

Go to **Change Approvals → Auto-Approval Rules → Add**. Same
object-type/constraint/priority matching as routing rules, plus which action
(create/update/delete/any) it applies to. A match is applied immediately
instead of being queued — a request is still recorded (marked auto-approved),
so there's always a full audit trail of what happened and why.

**Worked example: create and edit "planned" objects freely, but gate
promoting them to active.** Add a rule with **Object type** set to the model
(e.g. DCIM > Device; add one rule per model, since not every model has a
`planned` status, e.g. Prefix and VLAN don't), **Constraint**
`{"status": "planned"}`, **Action** `Any`, enabled. Then:

- creating a device with `status=planned` goes through immediately;
- editing a planned device goes through immediately, as long as it stays
  planned;
- changing its status to anything else (e.g. active) is queued for approval;
- deleting a planned device also goes through immediately with `Any`. Use two
  rules (`Create` and `Update`) instead if deletes should still be approved.

For **updates**, an auto-approval rule must match both the object as currently
stored *and* the values being submitted. That's what makes the last step work:
the stored device is planned, but the submitted one isn't, so the rule doesn't
match. Routing rules only look at the stored object.

### Choosing which object types are gated

By default every `NetBoxModel`-based object type is gated — see "What gets
gated" above. There are two ways to turn gating off for a specific type:

- **`exempt_models` in `PLUGINS_CONFIG`** (`configuration.py`) — a static list
  of `app_label.model_name` strings, e.g. `['dcim.cable']`. This controls
  registration at process start, so it needs a NetBox restart to take effect.
  Good for permanent, deploy-time exclusions you want version-controlled
  alongside the rest of your config.
- **Change Approvals → Exempt Models → Add** — pick an object type from a
  dropdown, same as any other rule table in this plugin. This is checked live
  on every single change, so toggling the **Enabled** checkbox takes effect
  immediately — no restart. Good for day-to-day adjustments an admin makes
  through the UI rather than editing a config file and restarting NetBox.

Both apply independently; a model exempted either way isn't gated.

**Exempting only some objects of a type.** An Exempt Models row can also have
a **Constraint**, in the same format as the rule tables, so only matching
objects skip approval. You can add several rows for the same object type.
Constraints can follow relations and many-valued properties, not just real
database fields. For example, to let anyone change cables in the Tampere
region without approval, add a row for **DCIM > Cable** with:

```json
[
  {"a_terminations__device__site__region__slug": "tampere"},
  {"b_terminations__device__site__region__slug": "tampere"}
]
```

(a list means "any of these", so a cable with either end on a device in a
Tampere site is exempt). For models with a direct site, it's simpler:
`{"site__region__slug": "tampere"}`. For an update, both the current object
and the new values must match, so moving a cable out of Tampere still needs
approval. Exempted changes leave no change request behind; use an
auto-approval rule instead if you want them logged.

### Getting notified of pending changes

No email/Slack code is built into this plugin — it reuses NetBox's own
**Event Rules**, since `ChangeRequest` is a normal change-logged NetBox object:

1. Go to **Operations → Event Rules → Add**.
2. **Object types**: Change Approval > Change Request.
3. **Event types**: `Object created` (fires when something is newly submitted —
   add a condition `{"attr": "status", "value": "pending"}` to skip
   auto-approved ones) or `Object updated` (fires on every status change; add a
   condition on `status` of `applied` or `rejected` to notify submitters of the
   outcome).
4. **Action**: a webhook, a script, or a notification group — whatever NetBox
   already supports.

See NetBox's own docs on Event Rule conditions for the exact condition syntax.

## Limitations (current version)

- **UI only.** Changes made via the REST or GraphQL API are not gated. This is
  the top roadmap item — see `DEVELOPER.md`.
- **Tags aren't captured in the diff.** `django-taggit` tag assignment isn't a
  plain Django many-to-many field, and isn't currently diffed/gated — tag
  changes on a gated object apply immediately, uncaptured by approval.
- **Rejection is a hard discard.** There's no "request changes, let the
  submitter revise and resubmit" flow yet; a rejected request is just closed
  with a comment, and the submitter has to start over.
- **Constraints on brand-new objects are best-effort.** A routing/auto-approval
  rule's constraint can only be checked against the database for objects that
  already exist. For a pending *create*, there's no database row yet, so
  constraints are checked against whatever's already set in memory on the
  unsaved instance (this covers the common case — e.g. a `tenant` picked in the
  form — but can't follow relations that aren't assigned yet).
- **Management commands and scripts bypass gating.** Only requests that carry
  NetBox's `current_request` context (i.e. made through the web UI) are gated.
- **No retention policy.** Every change request — approved, rejected, auto-
  approved — stays in the database forever; there's no built-in pruning. Also
  worth knowing before years of use: approver groups become permanently
  undeletable once any historical request references them, and uninstalling
  the plugin doesn't clean up tags, permissions, or (unless you reverse-
  migrate) its own tables. See DEVELOPER.md "Long-term data lifecycle, and
  what happens if you uninstall this" before committing to this long-term.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
