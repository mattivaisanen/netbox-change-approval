# Changelog

## 0.4.0 (2026-10-25)

Upgrade: run `manage.py migrate`. It adds new fields (reason, schedule,
approving rule), creates the Default rule from your current default
approver group and expiry settings, and adds a database index.

### New features
- Live updates: the Pending Approvals widget and the sidebar's Approval
  Inbox count now refresh on their own every 10 seconds, without reloading
  the page. Only a tab you are looking at checks, and it checks at once
  when you come back to it. The widget is only redrawn when its requests
  changed, and waits while a dialog or a row's details are open. Every
  change is animated: new rows fade in with a blue highlight, changed rows
  flash amber, rows that left (or that you marked as read) slide out, and
  a changed count pops once. No motion if your system asks for reduced
  motion.
- Pending Approvals widget: your rejected and expired requests now show on
  the dashboard widget, with who decided and their comment. Click the eye
  ("Mark as read") to take one off the widget and out of the inbox count;
  opening the request does the same. The Approval Inbox's "Rejected or
  expired" rows have the same button.
- Change Requests tab: every gated object's page has a "Change Requests"
  tab (before Changelog) listing that object's requests, pending and
  decided, with a count. It shows only when there are any.
- `manage.py prune_change_requests --older-than DAYS` deletes finished
  requests (applied, rejected, retracted, expired) older than that, with
  their approvals, comments and field work. Pending and sent-back requests,
  and ones with field work still open, are kept. `--dry-run` only counts.
- Notifications say who did it: each one in the bell's drop-down has a
  "By: <user>" line, and the Notifications page has a "By" column. This
  covers NetBox's own notifications too, such as an Event Rule's "Object
  created" on a change request.
- Default rule: Approval Rules now ends with a "Default rule" row that
  decides every change no other rule matches. Edit it like any rule to set
  the approver group, approvals needed, reason required and expiry for
  those changes. It always needs approval and can't be deleted or disabled.
  "Needs approval" rules that leave the approver group or expiry blank use
  the Default rule's values.
- Approve now, apply later: the Approve dialog has an optional "Apply at"
  time (a maintenance window, say). The last approval then schedules the
  change instead of applying it: it stays pending and locked, shows its time
  on the request and in the inbox, and NetBox's background worker applies it
  when due (every five minutes). Approvers can apply it now or cancel the
  schedule; if it can't be applied when due, the schedule is dropped and the
  submitter and scheduler are notified.
- Every request carries a reason: NetBox's "Changelog message" box (and
  `changelog_message` in the API) is saved as the request's reason and shown
  on the request page, in the inbox, in the approvers' notification and in
  webhook data (`reason`). An approval rule can require one ("Reason
  required"), e.g. for a ticket number; the change is refused until it's
  filled in.
- A small button next to the Approval Inbox starts a change set (or opens
  the one you're working in). Hidden for superusers and users who skip
  approval. While a change set is active the button glows softly (none
  with reduced motion turned on).
- Approve, Reject, Send back, Retract and Discard open as a pop-up dialog
  over the page you're on, like NetBox's own delete dialog, instead of a
  separate page. So do Approve Selected and Reject Selected. The separate
  pages still work for direct links and browsers without JavaScript.
- Discarding a change set and marking field work done ask in the same
  pop-up style instead of the browser's plain OK/Cancel box.
- `manage.py prepare_uninstall`: run it before removing the plugin. It
  deletes notifications, bookmarks and subscriptions about change requests
  and removes the Pending Approvals widget from dashboards, which would
  otherwise give server errors on the Notifications, Bookmarks and
  Subscriptions pages (and a reset dashboard) after removal. It also
  deletes changelog and journal entries and tag assignments of the plugin's
  own objects (which break the Changelog and Journal Entries pages and their
  API), and takes the plugin's object types off custom fields, custom links,
  export templates, saved filters, tags, event rules and permissions,
  deleting the ones that were only about change approval. It warns about
  requests still pending. See "Uninstalling" in the README.

### Improvements
- Animations follow the theme: the widget's idle drawings are drawn in the
  theme's text colour (black or white), with their highlights (blinking
  lights, check marks, sun, moon, beak, bobber) in NetBox's main and
  secondary colours. Live-refresh highlights use the same pair: new rows
  fade in from the main colour, changed rows flash the secondary colour.
- Colours follow NetBox and its theme: request and change set status badges
  now use NetBox's own status colour handling, so a theme plugin that
  recolours statuses (for example "Pending" or "Applied") recolours them in
  the widget, inbox, lists and request pages too. Informational badges
  ("batch", "auto-approved", "active", change set links) use NetBox's main
  colour instead of a fixed blue, and rule outcome and "scheduled" badges
  use NetBox's success/warning colours instead of fixed green/yellow.
- Pending Approvals widget: a bulk edit, import or change set is now one
  row for approvers and in your sent-back list too, as in the Approval
  Inbox, instead of one row per object. The row reads like "Edit 3
  objects" with a batch badge; Approve, Send back and Reject decide the
  whole batch, and the arrow lists every object with its changes (with a
  Revise button for each one in a sent-back batch). Rejected batches use
  the same title.
- Colour themes: the plugin's buttons and badges stay readable under any
  NetBox colour theme, light or dark. When a theme makes an icon or label
  too close to its button's colour (say, a white button with a white
  icon), it switches to the theme's text or background colour, whichever
  stands out more. When a button is nearly the same colour as what is
  behind it, it gets a thin outline; otherwise buttons look exactly as the
  theme draws them. Follows light/dark switches and live updates.
- Confirmation dialogs (approve, reject, send back, retract, discard and the
  bulk approve/reject dialogs) are spaced like NetBox's own dialogs: even
  gaps between their parts, no extra empty space above the buttons, and the
  "Reason" label sits above its box without a colon. The reason box now
  also shows its help text.
- Times in the Pending Approvals widget and the Approval Inbox read "4 hours
  ago" instead of a bare "4 hours". The full date and time shows on hover.
- Approval Inbox and Pending Approvals widget: row buttons and text no
  longer sit flush against the card edge; they line up with the card header.
- Pending Approvals widget: when nothing waits on you, it shows one of twenty
  small animated drawings (a server rack on a coffee break, a cat asleep on
  a switch, a tumbleweed and more) with a light-hearted line, both picked at
  random on each visit, instead of a plain sentence on an empty card.
  Some have a twist: the clock's minute hand falls off, the rocket never
  quite makes it, and the paper plane loops the loop. Approvers and requesters get different lines.
  It follows the light and dark theme and stays still for users who turn
  off animations.
- Sidebar: the change set button now sits left of the Approval Inbox, and
  the count has its own box on the right, the same size as the change set
  button, in the same outlined style. When approvals need you it has an
  outline, a faint fill and a slow glow inside it, in NetBox's main colour;
  with field work only it is gray. No box shows when nothing is waiting.
- Saving an approval rule checks its constraint: a misspelled field
  (`{"stauts": "planned"}`) or a wrong kind of value (`{"site": "lab"}`) is
  refused with an explanation, instead of the rule silently never matching.
- `prepare_uninstall` also deletes custom fields whose values are change
  requests or rules (they keep NetBox from cleaning up after the plugin),
  takes the plugin's events off event rules, and cancels its queued
  background jobs.
- The change request list loads faster when it shows many auto-approved
  requests.
- Auto-approved changes show the rule that approved them as the approver:
  on the request page, in the Approval Inbox and request list, in activity
  messages, and in the API (`auto_approved_by_rule`). The name stays even if
  the rule is later renamed or deleted. Older requests keep showing "Rule".
- The Approval Inbox button counts your open field work too. The badge is
  red when approvals need your attention and gray when only field work is
  left.
- The default approver group and request expiry moved from the Settings
  page into the Default rule, so the Settings page only holds plugin-wide
  options. Upgrading copies the values in effect into the Default rule.
- Marking field work done from the Approval Inbox keeps your place on the
  page instead of jumping down to the field work section.
- The "Working in change set" bar no longer shows on the change set's own
  page, whose header already has the same buttons.

### Bug fixes
- Approving, sending back, rejecting or discarding a request from the
  Pending Approvals widget now brings you back to the dashboard instead of
  the Change Requests list.
- A scheduled change could still be applied after it was retracted or
  rejected, if that happened while the background job was applying it, and
  clicking "Apply now" while the job ran could apply it twice. It is now
  checked again right before applying.
- A notification about a cancelled or failed schedule now says who
  scheduled it on its "By" line.
- `migrate netbox_change_approval zero` no longer brings back the old
  "Pending Approval" tag on objects with a pending create.
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
