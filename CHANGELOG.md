# Changelog

## Unreleased

### New features
- The Pending Approvals dashboard widget has a quick view of each request's
  changes, like the Approval Inbox.
- The Approval Inbox has pages: 50 items per section, with Previous/Next
  links. A bulk action stays one item on one page.

### Bug fixes
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

### Improvements
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
