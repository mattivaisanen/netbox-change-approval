# REST API

The plugin has a read-only REST API under `/api/plugins/change-approval/`,
for dashboards (Grafana, Power BI, ...), reports and scripts. Change
requests and rules are decided and edited in the web UI only, so the API
accepts `GET` only.

| Endpoint | What |
| --- | --- |
| `/api/plugins/change-approval/change-requests/` | Change requests |
| `/api/plugins/change-approval/approvals/` | Individual approvals (one per approver and revision) |
| `/api/plugins/change-approval/rules/` | Approval rules |

Authenticate with a NetBox API token, as for NetBox's own API
(`Authorization: Bearer <token>`, or `Token <token>` for older v1 tokens).
Results are paged like NetBox's (`limit`, `offset`, `count`, `next`). On
change requests and rules, `?fields=`, `?omit=` and `?brief=true` work as
on NetBox's own endpoints.

## Permissions

- Change requests: *View* on *Change Approval > change request*. Constraints
  apply, e.g. `{"submitted_by": "$user"}` shows a user only their own.
- Approvals: no permission of their own. A user sees the approvals of the
  change requests they may view.
- Rules: *View* on *Change Approval > approval rule*.

A read-only token for a dashboard user with only these view permissions is
enough.

## Filters

Change requests:

| Filter | Example |
| --- | --- |
| `status` (repeatable) | `status=pending&status=returned` (pending, applied, rejected, returned (sent back), retracted, expired) |
| `action` | `action=delete` (create, update, delete) |
| `submitted_by_name` (username), also `__ic`, `__isw`, ... | `submitted_by_name=alice` |
| `submitted_by`, `decided_by` (user id) | `submitted_by=4` |
| `decided_by_name`, `approver_group_name` | `approver_group_name=Network team` |
| `submitted_at`, `decided_at`, `applied_at`, `expires_at`, `created`, `last_updated` with `__gte`, `__lte`, `__gt`, `__lt` | `submitted_at__gte=2026-01-01T00:00:00Z` |
| `content_type` (object type) | `content_type=dcim.device` |
| `auto_approved`, `revision`, `required_approvals`, `batch_id` | `auto_approved=true` |
| `q` (object name contains) | `q=sw1` |

Approvals: `change_request` (id), `user` (id), `user_name`, `revision`, and
`created` with `__gte`/`__lte`.

Rules: `enabled`, `priority`, `content_type`, `action`, `outcome`,
`approver_group` (id).

The full list is in NetBox's API documentation at `/api/schema/swagger-ui/`.

## Fields

A change request has, among others: `id`, `url` (API), `display_url` (web
page), `object_type` (`app.model`), `object_id`, `object_repr`, `action` and
`status` (each `{"value", "label"}`), `submitted_by` and `decided_by` (each
`{"id", "username"}`; `id` is null once the user is deleted),
`submitted_at`, `decided_at`, `applied_at`, `expires_at`, `approver_group`
(name), `required_approvals`, `auto_approved`, `auto_approved_by_rule` (name),
`revision`, `reason`, `decision_comment` and `diff` (requested changes).

Time to decision is `decided_at` minus `submitted_at`.

## Grafana

With the [Infinity](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/)
data source:

1. Add an Infinity data source. Under *Authentication* choose *API Key
   Value pair*, key `Authorization`, value `Bearer <token>` (or `Token <token>` for a v1 token), *Header*, and
   add your NetBox address to *Allowed hosts*.
2. In a panel, type *JSON*, URL for example
   `https://netbox.example.com/api/plugins/change-approval/change-requests/?limit=1000&submitted_at__gte=${__from:date:iso}&submitted_at__lte=${__to:date:iso}`,
   *Rows / Root* `results`.
3. Add columns such as `submitted_at` (time), `status.value`,
   `submitted_by.username`, `object_type`, and group or count them in the
   panel (e.g. a bar chart of requests per status, or per requester).

For more than 1000 rows, raise `MAX_PAGE_SIZE` in NetBox's configuration or
use the data source's pagination (offset, `limit`/`offset` parameters).
