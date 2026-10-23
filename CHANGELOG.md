# Changelog

## Unreleased

### New features
- A small button next to the Approval Inbox starts a change set (or opens
  the one you're working in). Hidden for superusers and users who skip
  approval. While a change set is active the button glows softly (none
  with reduced motion turned on).

### Improvements
- The "Working in change set" bar no longer shows on the change set's own
  page, whose header already has the same buttons.

### Bug fixes
- A delete request records what it removes: every field with a value and,
  for cables, both ends ("A side: sw1 eth1"). Deleting a plain cable
  recorded nothing before, so its request said "No field-level changes
  recorded".

## 0.3.3 (2026-10-23)

### New features
- Webhook data for change requests says who should be told: `recipients`
  (username, name, e-mail) and `recipient_emails` (semicolon-separated), plus
  `link`, a full link to the request when the new `netbox_url` setting is
  set. On NetBox's own "Object created/updated" events the recipients follow
  the request's status (approver group while it waits, submitter once
  decided).
- Setup guide [docs/webhooks.md](docs/webhooks.md): Event Rule and webhook
  for any service, every data field, and Power Automate examples for an
  Outlook e-mail and a Teams message to each person from the Flow bot.

## 0.3.2 (2026-10-23)

### Bug fixes
- Field work links could point at the wrong object when a name appeared in
  more than one task (a device type named like another device, say), and a
  moved device's name wasn't linked. Each task now links only the objects it
  names, left to right.

## 0.3.1 (2026-10-23)

Upgrade: run `manage.py migrate` (adds one column for work task links).

### Bug fixes
- An Event Rule on Change Request for "Object created/updated/deleted" (for
  example a webhook) crashed with "Could not determine serializer for
  netbox_change_approval.ChangeRequest". Change requests and approval rules
  now have serializers, so such rules and their webhooks work.

### Improvements
- Field work: device, rack, interface, IP and other names in each task link
  to their NetBox pages; marking a task done asks to confirm first; the
  inbox's "Your field work" shows only the jobs from your own requests
  (approvers no longer see everyone's). The work sheet page still lists all
  jobs you may see.
- The Pending Approvals widget's title opens the Approval Inbox (the extra
  link at the bottom is gone).

## 0.3.0 (2026-10-22)

Upgrade: see README "Upgrading". Run `manage.py migrate`: this release adds
and removes database tables (the old rule tables are gone; their rules were
moved to Approval Rules in 0.2.0).

### New features
- Request page: "Policy Rules" (the rule, ✓/✗ with approvals so far, eligible
  reviewers) and "Reviews" (each reviewer's Approved / Changes requested /
  Rejected, with time and comment) panels, and an "Add a Review" menu.
- Notifications for a partly approved request ("approved by bob (1 of 2)"),
  and every notification fires a NetBox event with the request's details and
  recipients, so Event Rules can e-mail or post it through a webhook.
- The Pending Approvals widget links to the Approval Inbox.
- Change sets (Change Approvals → Change Sets): start one ("Install sw1 in
  rack A3", optional ticket), make your changes as usual, then submit them
  as one item that approvers decide together. Drafts stay hidden from
  approvers until submitted. `change_sets: False` turns it off.
- Settings page (Change Approvals → Settings, superusers only): change the
  plugin's settings in NetBox instead of `configuration.py`. Each setting
  shows whether its value comes from `configuration.py`, the default or the
  page, and a saved value can be switched back to `configuration.py`.
  Changes apply at once, without a restart. `exempt_models`, the emergency
  off switch, stays `configuration.py`-only and is shown read-only.
- Field work sheet (a "Field work" section in the Approval Inbox): approved changes that
  need hands on site become plain tasks ("Mount sw1 in rack A3, U20–21",
  "Patch sw1 eth1 → pp1 port 3", "Configure 192.0.2.1/24 on sw1 eth0", moves,
  removals, modules, VMs), grouped by site
  and rack, to tick off; "Work sheet by rack" opens a printable page. Rack
  and site pages show open tasks.
  `work_sheets: False` turns it off.
- Backup approvers: on the new "Away / Stand-in" page an approver names a
  stand-in and the days they're away. Meanwhile the stand-in can decide
  requests routed to the away approver's groups, sees them in their
  Approvals list, inbox and dashboard widget, and gets the notifications
  instead.
- A device request that puts a device in a rack, takes it out or moves it
  shows the rack on the request page. For a move, the rack elevation marks
  the requested spot with a dashed outline, so the approver sees the fit and
  the neighbours.
- A new cable with an end on a new device that is still waiting for approval
  joins that device's request: its submitter can patch it freely, everyone
  else waits, and approving the device approves the cables. Its request page
  counts them.
- The Approval Inbox has an "All change requests" button.
- The Pending Approvals dashboard widget has a quick view of each request's
  changes, like the Approval Inbox.
- The Approval Inbox has pages: 50 items per section, with Previous/Next
  links. A bulk action stays one item on one page.

### Bug fixes
- The field work sheet only shows, and lets you tick off, jobs from
  requests you're allowed to see; before, any logged-in user saw and could
  tick every job.
- Starting a change set only returns to pages on this site (a crafted link
  could send you elsewhere).
- Rejecting several requests at once (a batch, or Reject Selected) no
  longer crashes when one new object depends on another (a new device in a
  new rack): newer objects are removed first. If an object can't be removed
  because something else uses it, nothing changes and the message says what
  is in the way.
- NetBox's change log named the approver as the user of an approved,
  rejected, sent-back or expired change. It now names the person who asked
  for the change; the message already says who decided ("Approved by ...").
- A new device's work sheet only listed mounting it: cables patched to it
  while it waited for approval got no task. Approving it now lists them too.
- "All Change Requests" in the Change Approvals menu was highlighted on every
  page under it (Approval Inbox, Approvals), whatever page was open.
- Two decisions on the same request at the same moment can no longer
  overwrite each other. Before, a rejection landing just after an approval
  could mark the applied change as rejected, and for a new object delete it.
  The second decision now changes nothing and says the request is already
  decided. Deciding a batch is all or nothing.
- The Changelog and Journal tabs of a change request showed what it asks for
  to users who can't open the request itself. They now follow the same rule
  as the request page.
- Files and images uploaded in an edit that waits for approval (a device
  type's images, a plugin's documents) were never stored, so approving the
  edit linked the object to a missing file. They are stored now, and deleted
  again if the request is rejected, withdrawn or expires, or the upload is
  replaced.
- The Approvals menu didn't show for members of a default approver group
  created (or renamed) after NetBox started, until a restart.
- The notification bell showed a red dot but an empty drop-down for anyone
  with a change request notification: the drop-down failed to load. It now
  lists them.

### Improvements
- Notifications say who did what ("rejected by bob: wrong rack", "bob: why
  this VLAN?"). Comment notifications go only to the submitter and the
  approver group, no longer to everyone who commented before.
- Approval Inbox and dashboard widget titles say what is requested and on
  what: "Create Rack R1", "Edit Device Access switch sw1" (devices and VMs
  include their role). Site, rack and the field changes are in the quick
  view.
- The README, configuration reference and developer guide cover change
  sets, the Field work section of the inbox, backup approvers, the rack
  preview, cables that follow a new device, change log labels for parts of
  a pending device, the compact inbox and widget, the change request page
  and the Settings page.
- Changes to the interfaces, cables and other parts of a new device that is
  still waiting for approval are labelled in the change log ("Part of
  change request #N, waiting for approval"), and no longer add a
  "Resubmitted for approval" row each time.
- Approval Inbox and dashboard widget are more compact: one line per
  request, with columns for the request, its object type, who it's from (or
  waiting on, or who decided), and how long ago, instead of two tall lines
  with an empty middle. Columns keep their place when a quick view opens,
  and ages show one unit ("10 hours"; hover for the exact time).
- Approval Inbox and dashboard widget: clicking anywhere on a request's
  row (except its links and buttons) opens its quick view, and the arrow
  points up while it's open (it never turned before). Closing a quick view
  no longer lags.
- The small change tables (batch pages, Approval Inbox quick view,
  dashboard widget) line up: same column widths in every table, every
  empty value shown as the same grey dash, and new objects show just their
  values without an empty Old column.
- The change request page is easier to decide from: one "Changes"
  table (now → requested, with "(empty)" instead of a blank), a line with
  the object's site and rack, details and comments on the side, and only
  the buttons that act on the request (no Edit, Delete, Bookmark or
  Subscribe). The title names the request and object; whole numbers no
  longer show as 10.0.
- Removed the old Approval Routing Rules, Auto-Approval Rules and Exempt
  Models pages, which Approval Rules replaced earlier. Upgrading deletes
  their old database tables and change log entries; their rules were
  already copied into Approval Rules.
- The Approvals list, Approval Inbox and Pending Approvals widget no longer
  run extra database queries for every request shown, so they stay fast
  with many pending requests.
- Opening the approve or reject page of a request that was already decided
  explains that instead of showing "not found".
- The configuration reference lists every setting; README covers inbox
  pages and file uploads in held edits.
- Tests run on NetBox 4.7.0 as well as 4.7.3.

## 0.2.2 (2026-10-22)

Fixed:
- Opening the Approval Inbox (and other request pages) failed with
  "from_db() got an unexpected keyword argument 'fetch_mode'" on newer
  Django releases.

## 0.2.1 (2026-10-22)

Fixed:
- NetBox failed to start when the netbox-branching package was installed
  but not enabled as a plugin.

## 0.2.0 (2026-10-22)

Upgrade: see README "Upgrading". Run `manage.py migrate`: this release adds
database changes.

New:
- Rules can require more than one approver, and pending requests can expire
  (per rule, or the `expire_pending_after_days` setting).
- Comments on change requests, and NetBox notifications for new, decided,
  sent-back and expired requests.
- Submitters can update their own pending request from its edit page.
- Pending objects show whether they are new, edited or deleted; components
  of a pending new device belong to the device's request.
- The REST API is gated: JSON responses (202 submitted, 409 already
  pending) instead of redirects; see README.
- Works alongside netbox-branching (approval covers main only).

Fixed:
- Nobody can approve, reject or send back their own request.
- Only superusers can change approval rules.
- Deleting an object queues that object, not one of its dependents.
- Tag changes, bulk "Add/Remove ..." fields and links edited from the
  other side (the ASN form's "Sites") need approval; approving tag changes
  no longer fails.
- API creates, objects created by scripts or as a side effect of a form,
  and import rows that only move an IP/MAC to another interface need
  approval.
- Bulk actions run as background jobs are handled per object.
- Deciding a bulk batch only decides the requests routed to you; every
  approver group in a batch is notified.
- Removing the tag a "skip approval" rule looks for needs approval.
- Approval pages only accept return links on this site.
- The request list follows view permission constraints.
- No database queries while NetBox starts.
- Requires NetBox 4.7 (it never worked on 4.2 as claimed).

## 0.1.0

First version.
