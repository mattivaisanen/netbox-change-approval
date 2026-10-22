# Changelog

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
