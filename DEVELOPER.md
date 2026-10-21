# Developer guide

This covers the architecture and the reasoning behind it — read this before
changing how gating works, not just what it looks like.

## The constraint that shaped everything

The brief this plugin was built against: gate changes to *any* NetBox object —
core models and other plugins' models alike — without maintaining a per-model
list, without depending on another plugin (notably not `netbox-branching`,
whose schema-per-branch approach is far heavier than "queue this edit"), and
without forking/monkey-patching NetBox's core views. Every design decision
below follows from trying to satisfy all of that at once using only
**documented, version-stable NetBox plugin extension points**.

Three NetBox facts made this possible, first confirmed against NetBox 4.2
source and since re-checked against NetBox 4.7, the only version the plugin
is tested on and the minimum it supports
(not just docs — see the comments in the modules below for exactly where):

1. **`CustomValidator`** (`extras/validators.py`) runs inside
   `NetBoxModel.clean()` for every model listed in the `CUSTOM_VALIDATORS`
   setting, *before* a create/update is saved, and receives the current
   request. Raising `ValidationError` there is caught natively by Django's
   `ModelForm` — it just shows up as a normal form error. No view overriding
   needed.
2. **`netbox.context.current_request`** is a `ContextVar` NetBox itself
   populates for the lifetime of a request (it's how NetBox attributes
   change-log entries and webhook payloads to a user). It's readable from
   anywhere, including a plain Django signal receiver — which is what makes
   gating deletes possible without a request object being threaded through.
3. **`PluginConfig.middleware`** is a normal Django middleware hook. Nothing
   plugin-specific about `process_view`/`process_exception` once registered
   this way.

Given those three, "gate everything, no model list" becomes: discover every
`NetBoxModel` subclass at startup, register our validator for `CUSTOM_VALIDATORS`
on all of them, and connect one `pre_delete` receiver to all of them.

## Module map

| Module | Responsibility |
|---|---|
| `discovery.py` | The *only* place that decides "is this model gated". Everything else (validators, delete signal, the detail-page banner) sources its model list from here. |
| `matching.py` | `rule_matches(rule, instance)` — the constraint-matching engine shared by `ApprovalRoutingRule` and `AutoApprovalRule`. Reuses `utilities.permissions.qs_filter_from_constraints`, the same helper NetBox's own `ObjectPermission` constraint evaluation uses, so the JSON filter-dict format is identical to what operators already know from the Permissions UI. |
| `models.py` | `BaseMatchRule` (abstract), `ApprovalRoutingRule`, `AutoApprovalRule`, `ExemptModel`, `ChangeRequest`. All are `NetBoxModel` subclasses — which is also how `ChangeRequest` gets change-logging and Event Rule support "for free" (see "Notifications" below). |
| `serialization.py` | Generic field-level snapshot/diff/apply. Deliberately *not* DRF-serializer-based — see "Why field-level, not DRF serializers" below. |
| `context.py` | Two `ContextVar`s: `applying_change` (the escape hatch so an *approved* write isn't gated again) and `current_batch` (groups one bulk action's `ChangeRequest` rows under one `batch_id`). |
| `gating.py` | `gate(instance, action, request)` — the single decision function both the validator and the delete signal call. Locking, bypass, auto-approval, routing all live here exactly once. |
| `validators.py` | `ApprovalGateValidator`, a `CustomValidator` subclass — the create/update entry point. |
| `signals.py` | `pre_delete` receiver — the delete entry point. Connected once per gated model at `ready()` time. |
| `middleware.py` | Opens `current_batch` around bulk edit/import/delete requests (detected by resolving the URL ourselves, since `resolver_match` isn't populated yet this early); catches `ChangeApprovalRequired` to turn an aborted delete into a redirect + message instead of a 500. |
| `permissions.py` | The bypass check — builds the exact permission string NetBox's own `ObjectPermissionBackend` expects (`<app_label>.bypass_approval_<model>`) and calls `user.has_perm()`. No new permission infrastructure. |
| `views.py`, `forms.py`, `tables.py`, `filtersets.py`, `urls.py`, `navigation.py`, `template_content.py` | Standard NetBox UI scaffolding (`@register_model_view` + `get_model_urls()`), one `PluginTemplateExtension.alerts()` banner shared across every gated model. |
| `widgets.py` | `ApprovalsWidget`, a custom `extras.dashboard.widgets.DashboardWidget` — see "The dashboard widget" below for two real gotchas hit building it. |
| `table_styling.py` | Monkeypatches `NetBoxTable.configure()` to highlight any row with a pending `ChangeRequest`, across every model's list view generically — see its own module docstring. |
| `rack_styling.py` | Monkeypatches `dcim.svg.racks.RackElevationSVG` to draw the same pending-highlight around devices in a rack elevation. A separate patch target from `table_styling.py` because the elevation is a standalone SVG document embedded via `<object>`, not part of the host page's DOM — see its own module docstring. |

## Why field-level, not DRF serializers

By the time `ApprovalGateValidator.validate()` runs, Django's `ModelForm` has
already called `construct_instance()` — every scalar and FK value from the form
is already set on the in-memory instance's attributes. NetBox's own
`NetBoxModelForm` additionally stashes pending many-to-many values on
`instance._m2m_values` specifically so custom validators can see them (look at
`CustomValidator._get_instance_attr` in NetBox core — this isn't a plugin-side
hack, NetBox built this for exactly this use case).

So "snapshot the new state" is just "read `instance`'s own attributes", and
"apply an approved change" is just "set those attributes on a fresh/fetched
instance and call `.full_clean()` + `.save()`" — the same thing every NetBox
`ModelForm` already does. Driving a generic DRF serializer per model instead
would mean fighting every model's custom `create()`/`update()` overrides,
nested writable fields, and serializer-specific validation quirks — fragile
across hundreds of heterogeneous models. Tags (`django-taggit`) need no
special case: NetBox puts them in `_m2m_values` like any other many-to-many
field. They're applied as `Tag` objects, since taggit's `set()` refuses bare
pks. Bulk edit's "Add ..."/"Remove ..." fields are folded into `_m2m_values`
before validation by `bulk.py`, since NetBox itself only applies them after
the save.

Type coercion on the way back in (`_coerce` in `serialization.py`) uses each
Django field's own `.to_python()` — the same method Django forms use to turn a
submitted string back into a `date`/`Decimal`/etc. — rather than hand-rolling
per-type parsing.

## Why `CUSTOM_VALIDATORS` is *merged*, not replaced

`CUSTOM_VALIDATORS` is technically one of NetBox's dynamic, DB-backed
`Config` parameters — but `Config.__getattr__` checks
`hasattr(settings, 'CUSTOM_VALIDATORS')` *first*, before falling back to the
database value. So setting it in `apps.py`-equivalent (`__init__.py: ready()`
→ `discovery.wire_up_gated_models()`) takes priority unconditionally. If we
*replaced* `settings.CUSTOM_VALIDATORS` outright, any validators an operator
configured themselves in `configuration.py` — for their own, unrelated
purposes — would silently stop running. `wire_up_gated_models()` reads
whatever's already there and appends to each model's list instead.

One consequence worth knowing: once this plugin is installed, NetBox's own
*dynamic* Configuration Revisions UI can no longer be used to change
`CUSTOM_VALIDATORS` at runtime — the hard-coded `settings` value always wins.
This is called out in `discovery.py` and is a NetBox-level behavior, not
something this plugin can route around.

## NetBox's `Group` is not Django's `Group`

Caught by deploying against a real instance (NetBox 4.7): `ApprovalRoutingRule.
approver_group` and `ChangeRequest.approver_group` must be FKs to
**`users.Group`**, NetBox's own standalone group model
(`users/models/users.py`) — not `django.contrib.auth.models.Group`. NetBox
replaced the stock one outright (it adds a `description` field and ties into
`ObjectPermission`); `User.groups` and the Admin UI populate `users.Group`
specifically, and assigning a `django.contrib.auth.models.Group` instance to a
NetBox user's `.groups` raises `TypeError` immediately (Django's M2M descriptor
checks the exact model, not just table compatibility). If you're adding a new
FK to a group anywhere in this plugin, import `Group` from `users.models`, not
`django.contrib.auth.models`.

## Delete gating and the transaction problem

Caught by running the test suite against a real instance, not by reasoning
about it up front: a pending (non-auto-approved) delete's `ChangeRequest` is
deliberately **not saved** inside `signals.gate_delete` / `gating.gate()`.

NetBox's delete collector (`netbox/models/deletion.py`) wraps the entire
`pre_delete` signal dispatch — where our gate runs — in its own
`transaction.atomic(savepoint=False)`. If we saved the `ChangeRequest` there
and then raised to abort the delete, that raise rolls back *the whole atomic
block*, taking our own just-saved row down with it: the delete gets aborted
(correct) but the record that it was ever queued vanishes too (wrong — no
audit trail, nothing for an approver to act on).

The fix: `gate()` returns the `ChangeRequest` **unsaved** for this case, it
rides along on the `ChangeApprovalRequired` exception, and
`ChangeApprovalMiddleware.process_exception()` is where it actually gets
`.save()`d — by the time an exception reaches a middleware's
`process_exception` hook, the atomic block that raised it has already finished
rolling back, so this is a fresh, independent write, unaffected by that
rollback. In a real request (no surrounding `ATOMIC_REQUESTS` or explicit
`transaction.atomic()` around the view — confirmed absent in NetBox's
`ObjectDeleteView`/`BulkDeleteView`), that atomic block is the outermost one on
the connection, so this is also exactly why `tests/test_validators_and_signals.
py`'s `GateDeleteSignalTests` uses `TransactionTestCase` instead of `TestCase`:
plain `TestCase` wraps every test in its *own* outer atomic, which would make
even a *correctly* rolled-back delete poison every assertion after it — an
artifact of the test wrapper, not of production behavior.

The same reasoning is why auto-approved deletes and creates are handled
differently from auto-approved updates. An auto-approved **update** calls
`ChangeRequest.apply()`, which performs the write via a *separate*
reconstructed instance (`serialization.apply_update`), so aborting the
original in-flight save afterward is safe — `gate()` returns a saved
`ChangeRequest` and the validator raises to block the duplicate write. For a
**delete**, that "separate" write would mean calling `.delete()` again on the
*same row*, re-entrantly, from within its own `pre_delete` signal — collector
state isn't designed for that. For a **create**, there's no "separate"
instance to speak of — the whole point of the CREATE path is that the
original save *is* the approved write (see "Create is different", below).
So both auto-approved delete and auto-approved create just log a
`ChangeRequest` (`status=applied`) and return `None`, letting the original
write already in flight proceed as the approved action, instead of calling
`apply()` at all.

## Create is different: written immediately, not queued

Every other action in this plugin follows "capture a diff → abort the write →
apply it later on approval." **Create does not.** The reason is concrete: if a
pending create were invisible until approved, a second person browsing Racks
(or Devices, or IPs) while the first request is still waiting has no way to
know it exists, and creates a duplicate. There's no equivalent risk for
update/delete — the object they'd be looking at already reflects its current,
real state regardless of a pending change.

So for CREATE, `gating.gate()` does not return a `ChangeRequest` to abort the
write. Instead it stashes an **unsaved** `ChangeRequest` on
`instance._nbca_pending_create` and returns `None` — telling the validator
"let this save proceed exactly as given." The form's own `.save()` runs
normally, Django's `post_save` signal fires, and
`signals.finish_pending_create()` (connected in `signals.connect()` alongside
the delete gate) is where the second half happens: the marker is popped off
the instance, and the `ChangeRequest` gets the real `object_id` and is saved.
List views show the object as pending via the row outline (`table_styling.py`).
(Earlier versions also tagged it "Pending Approval"; migration 0004 removes
that tag.)

Because the object is already real, `ChangeRequest.apply()` for a CREATE does
no write at all — it just closes the request. `ChangeRequest.reject()` for a
CREATE has to do the opposite of every other action: since something *was*
actually written, rejecting it means deleting that real row, or it would be
stuck behind a closed, un-actionable request forever.

The object-lock check (`ChangeRequest.is_object_locked()`) runs identically
for a pending create as for anything else — once created, the object can't be
edited again until the create itself is decided.

One side effect worth calling out, since it answers a question that comes up
naturally: writing the object for real immediately means NetBox's *own*
validation (uniqueness constraints, `full_clean()`, DB constraints) runs
against a real row from the moment of creation — e.g. two people racking a
device in the same rack unit while both are still "pending" will have the
*second* placement rejected by NetBox's own rack-fit validation, not just by
this plugin's duplicate-prevention intent. The tag is a visibility aid, not
the only thing standing between two conflicting creates.

## Retraction: a fourth status, not a REJECTED alias

`ChangeRequest.retract()` (`views.ChangeRequestRetractView`) lets the original
submitter withdraw their own still-pending request. It could have reused
`reject()` and the existing `REJECTED` status — the write-side handling is
identical (CREATE deletes the object, UPDATE/DELETE just close the request,
same as `reject()`) — but `RETRACTED` is a distinct `StatusChoices` value on
purpose: `REJECTED` means an approver looked at it and said no; `RETRACTED`
means the submitter changed their own mind before anyone decided. Collapsing
those into one status would lose that distinction in the audit trail (and in
any Event Rule conditions an operator sets up against `status` — see
"Notifications" above) for no real benefit.

The permission check is the inverse of approve/reject's: `_can_decide()`
checks approver-group membership; `ChangeRequestRetractView.check_permission()`
checks `change_request.submitted_by_id == request.user.pk` instead — a
different base-class override, not a variant of the same check. Same
deferred-to-the-view pattern as approve/reject's buttons (see tables.py's
`_DECISION_BUTTONS` comment): the Retract button shows on any pending row
regardless of who's viewing it, and the view itself redirects with an error
if you try to retract someone else's request.

## The locking model

"At most one pending `ChangeRequest` per object" is enforced *twice*, on
purpose:
- In application code (`ChangeRequest.is_object_locked()`, checked at the top
  of `gate()`), so a second editor gets a clean, friendly error instead of a
  database error.
- As a partial `UniqueConstraint` in the database
  (`nbca_unique_pending_change_per_object`, condition `status='pending'`), as
  the actual source of truth under concurrent requests — the application-level
  check has a TOCTOU race under concurrent submissions; the constraint is what
  actually prevents two pending requests existing for the same object. `NULL`
  `object_id` (pending *creates*) are correctly never constrained against each
  other — they're distinct not-yet-created objects, not the same row.

## Redirecting after a gated create

NetBox's generic `ObjectEditView` redirects to the newly-created object's own
detail page on success unless the request carried a `return_url`. For a gated
create, landing on a list instead lets someone add several objects in a row
without each one bouncing them off to a single-object page. There's no
per-model view to hook for this — creates for arbitrary gated models go
through whatever `ObjectEditView` subclass that model's own app (or another
plugin) defines — so `ChangeApprovalMiddleware` handles it generically: on a
GET to any gated model's add view with no `return_url` already present, it
redirects once to the same URL with `?return_url=<wherever the Referer header
says the user came from>` appended. NetBox's own form/view then honors that
`return_url` for both the Cancel button and the post-save redirect, with zero
further plugin code.

One gotcha worth knowing if you touch this: NetBox's own redirect-safety check
(`utilities.request.safe_for_redirect()`, called from every generic view's
`get_return_url()`) is `url_has_allowed_host_and_scheme(url, allowed_hosts=None)`
— passing `allowed_hosts=None` doesn't mean "any host is fine," it means *no*
host is ever acceptable, so it rejects **any absolute URL outright**, even one
pointing at the exact same host. Only a relative (path [+ query]) URL passes.
`_redirect_add_with_return_url` validates the Referer is same-origin using our
*own* `url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()})`
call (which, unlike NetBox's, does accept an absolute URL on a matching host),
but then strips the scheme/host via `urlparse` before handing it to NetBox as
`return_url` — otherwise NetBox's stricter check silently discards it and
falls back to the created object's own page, exactly the redirect this is
trying to avoid. (Caught live: a first version passed the raw absolute Referer
through, and it "worked" in one manual test purely by coincidence — the
fallback list-view URL happened to match the intended return_url. A second
test using a Referer with a distinguishing query string and inspecting the
actual redirect `Location` header, not just scanning for success, caught it.)

## Bulk actions

`ChangeApprovalMiddleware.__call__` resolves the current request's URL itself
(via `django.urls.resolve`) *before* calling `get_response()`, because
`request.resolver_match` isn't populated this early in the middleware chain —
the normal `process_view` hook would run too late to wrap the whole view
execution in a context manager. If the resolved view is one of NetBox's
`BulkEditView`/`BulkDeleteView`/`BulkImportView`, a fresh UUID is opened via
`context.batch_scope()` for the duration of the request. Every object that
bulk action touches gets its own `ChangeRequest` row (so locking and per-object
diffs stay simple), but all of them share that one `batch_id` — the approval
queue groups them, and approving/rejecting iterates every pending row sharing
the batch at once (`views._batch_targets()`).

## Notifications: no event-firing code, on purpose

`ChangeRequest` is a plain `NetBoxModel` subclass, which means
`object_created`/`object_updated` Event Rule events fire for it automatically —
the exact same mechanism NetBox uses for every other model's webhooks. There is
deliberately no custom `EventType` registration or event-firing code in this
plugin: operators configure a normal Event Rule against the `Change Request`
object type with a condition on `status`, exactly as they would for any other
NetBox object. If you're tempted to add a custom event type for this, don't —
it'd be strictly more code for strictly less flexibility (conditions can
already distinguish auto-approved vs. pending vs. rejected via the `status` and
`auto_approved` fields in the webhook payload).

## The top-level nav tab, and why the ticker moved to the Approvals page

`navigation.py` exports `menu` (a `netbox.plugins.PluginMenu`), not the more
common `menu_items` (a flat tuple). `PluginConfig._load_resource()` treats
these as two different resource names (`'menu': 'navigation.menu'` vs.
`'menu_items': 'navigation.menu_items'`) — a flat `menu_items` gets nested
under the generic "Plugins" dropdown via `register_menu_items()`, while a
`PluginMenu` assigned to `menu` gets its own top-level entry next to
"Organization"/"IPAM" via `register_menu()`. `__init__.py`'s `PluginConfig`
must point at whichever one you actually define — it previously pointed at
`'navigation.menu_items'`, which would now resolve to nothing if `navigation.py`
only exports `menu`.

There used to be an `ApprovalTicker` extension here too (`PluginTemplateExtension.
navbar()`, `models=None` so it rendered in the global top-right navbar icon
tray on every page — see `_get_registered_content` in NetBox core, which looks
up extensions registered under the `None` key for that specific hook). It's
gone now: the pending-count badge moved to `views.ApprovalsQueueView`'s own
page title instead (`get_extra_context()` returns `pending_count`;
`templates/changerequest_approvals.html` overrides `object_list.html`'s
`title` block to show it as a pill next to "Approvals").

This wasn't just a style choice — it's a hard constraint. `PluginTemplateExtension`
only exposes `head()`, `navbar()`, and a handful of object-list/detail-page
regions (`list_buttons()`, `buttons()`, `alerts()`, `left_page()`,
`right_page()`, `full_width_page()`); there is no hook for injecting dynamic,
per-request content into the sidebar/nav tree itself. That tree (rendered by
`utilities/templates/navigation/menu.html`) is built once from the static
`PluginMenu`/`PluginMenuItem` objects at Django startup and reused for every
request — `PluginMenuItem.link_text` is a plain string, not a callable, and
NetBox core's own nav (`netbox/navigation/menu.py`) has no precedent for a
dynamic badge inside it either. So "move the ticker onto the tab" could only
mean *the page you reach by that tab*, not the tab control itself — which is
what this does.

## Two exemption mechanisms, not one, on purpose

`discovery.iter_gated_models()` (the static `PLUGINS_CONFIG['exempt_models']`
list) and `models.ExemptModel` (a DB table, UI-editable under Change Approvals
→ Exempt Models) both exclude a model from gating, but at different points in
the request lifecycle, and that's deliberate rather than redundant:

- `iter_gated_models()` runs once, at `ready()`, and decides which models get
  a validator registered (`discovery.wire_up_gated_models()`) and a
  `pre_delete` receiver connected (`signals.connect()`). Changing the config
  list requires a NetBox restart before it takes effect — it's baked into
  `settings.CUSTOM_VALIDATORS` and Django's signal dispatch table at process
  start, neither of which re-reads config mid-process.
- `gating._is_exempt(model, content_type)` runs inside `gate()` itself, on
  *every* call, querying `ExemptModel` fresh each time. This is what makes the
  "Enabled" checkbox in the UI take effect immediately, with no restart —
  which is the entire point of building it as a DB table instead of just
  telling people to edit `PLUGINS_CONFIG` and restart.

A model can only be *exempted* via either path, never gated by one and exempt
via the other in a way that matters — `iter_gated_models()` exemption is
coarser (no validator/signal connected at all, so there isn't even a `gate()`
call to reach), `ExemptModel` exemption is finer (validator/signal stay
connected, `gate()` runs, and just returns `None` immediately). Both are
checked independently in their respective places; nothing unifies them into
one list, because they serve different operational needs (config-as-code vs.
day-to-day UI toggle) rather than being two ways to express the same thing.

## The dashboard widget, and two things that aren't bugs in this plugin

`widgets.py`'s `ApprovalsWidget` is a custom `extras.dashboard.widgets.
DashboardWidget` — registered via `@register_widget` the same way NetBox's own
`NoteWidget`/`ObjectListWidget`/etc. are, imported once from `__init__.py`'s
`ready()` purely to trigger that decorator. Unlike pointing NetBox's generic
`ObjectListWidget` at `ChangeRequest` (which works fine — confirmed live, it's
a real option in the picker), this one scopes to "routed to me, still pending"
via `permissions.approver_queue()` and links straight to approve/reject, which
is the actual point of putting this on a dashboard (see README "Task 9": a
widget to "approve right from there").

Two real failures surfaced while building and testing this, neither of them
bugs in this plugin's gating logic — both are NetBox-core dashboard quirks
worth knowing if you touch this file again:

**`DashboardWidget.__str__` requires an actual `str`, not a lazy translation
proxy.** It returns `self.title or self.__class__.__name__` with no
`str()`/`force_str()` wrapper. `self.title` comes from `default_title` if no
custom title was set — if that's assigned via `gettext_lazy`, Python's own
`__str__` dunder protocol rejects the lazy proxy outright the first time
anything calls `str()` on a widget instance: `TypeError: __str__ returned
non-string (type __proxy__)`. NetBox's own `extras/dashboard/widgets.py`
avoids this by importing the *non-lazy* `gettext as _` for every widget class
in that file — `widgets.py` here matches that for the same reason, not as a
general style preference (contrast with everywhere else in this plugin, which
correctly prefers `gettext_lazy` for anything evaluated at class-definition
time, per normal Django practice).

**A `Dashboard.config`/`Dashboard.layout` desync silently discards the entire
custom dashboard.** `Dashboard.get_layout()` iterates `self.layout` and does
`self.config[item['id']]` for each entry with no existence check — a `layout`
entry whose `id` no longer has a matching `config` key raises `KeyError`.
`HomeView.get()` wraps the *whole* `get_layout()` call in a bare `except
Exception`, and on any failure **silently falls back to the stock default
dashboard** for that request, with nothing worse than a vague "There was an
error loading the dashboard configuration" flash message — no indication
*which* widget, or that the user's actual custom dashboard (every widget they
ever added) is still sitting there in the DB, intact, just inaccessible until
the desync is fixed. Hit this firsthand mid-session: manually removing a
widget via `dashboard.config.pop(id)` in a Django shell (rather than
`Dashboard.delete_widget(id)`, which correctly updates *both* `config` and
`layout` together) left a dangling `layout` entry, and every subsequent
homepage load for that user silently reverted to defaults — which is also a
plausible explanation for "I can't see/add an approvals widget" style reports
in general: it doesn't take a plugin bug, just one inconsistent edit, for a
user's whole dashboard to vanish behind a generic error message. If you ever
need to manipulate `Dashboard.config`/`.layout` directly (tests, data fixes),
use `add_widget()`/`delete_widget()`, never edit either collection alone.

## Long-term data lifecycle, and what happens if you uninstall this

This plugin has no retention, archival, or pruning of its own — every single
change request, approved or rejected, from the day it's installed onward,
stays in `ChangeRequest` forever. Over months or years of real use this table
only grows. There's no built-in management command to prune old rows; if that
becomes a problem, it's a manual `ChangeRequest.objects.filter(status__in=
[APPLIED, REJECTED], decided_at__lt=...).delete()` (or an export-then-delete)
an operator would have to do themselves.

**Approver groups become permanently undeletable.** `approver_group` is
`on_delete=models.PROTECT` in *two* places — `ApprovalRoutingRule` and
`ChangeRequest` itself. Deleting the routing rule that used a group isn't
enough to free it up: every historical `ChangeRequest` ever routed through or
decided by that group *also* blocks deletion, forever. Confirmed firsthand
this session — trying to delete a test "Hardware Approvers" group failed with
a real `ProtectedError` naming old, already-applied `ChangeRequest` rows as
the blocker. There's no workaround short of deleting or reassigning that
history first; this is a deliberate trade-off (audit-trail integrity over
group-list tidiness), not a bug, but it means an org that renames/consolidates
approver groups over the years will accumulate groups it can never clean up.

**If a gated model itself goes away** (the plugin or app that defined it gets
uninstalled, or a future NetBox version drops it) and someone runs Django's
`remove_stale_contenttypes`, every `ChangeRequest` for that model's content
type is deleted too — `ChangeRequest.content_type` is a real FK to
`contenttypes.ContentType` with `on_delete=models.CASCADE` (it has to be, see
"Create is different" above re: the `contenttypes.E004` system check), and
`remove_stale_contenttypes` deletes the `ContentType` row once the model is
gone. That cascades silently; there's no warning.

**The object the request is *about* can disappear without any of this
breaking**, though — `object_id` is a plain integer (GenericForeignKey doesn't
support a real DB-level FK constraint across arbitrary target tables), so a
later DELETE of that object doesn't touch old `ChangeRequest` rows at all.
`target_object` just resolves to `None` from then on, and the UI already
accounts for that (`changerequest.html`: falls back to `object_repr`, the
string snapshot captured at submission time). `submitted_by`/`decided_by` are
`SET_NULL`, so a deleted user account degrades the same gracefully-blank way.

**Uninstalling the plugin entirely** — removing it from `PLUGINS`, uninstalling
the package — stops all gating immediately, but doesn't touch the database by
itself:
- Any `ChangeRequest` for a pending **update or delete** at that moment just
  never gets applied. The original edit/delete was blocked and stays blocked
  forever (nothing errors — the object just keeps whatever state it already
  had). Reapplying it, if still wanted, is a manual job.
- Any object that was mid-flight on a pending **create** simply stays, as a
  normal object (nothing marks it as pending once the plugin is gone).
- The three DB tables (`ChangeRequest`, `ApprovalRoutingRule`,
  `AutoApprovalRule`) are **not** dropped automatically. Without running
  `python manage.py migrate netbox_change_approval zero` first, they're left
  behind as orphaned-but-harmless dead weight (Postgres doesn't enforce any
  "owning app" relationship, so nothing breaks — they just sit there). Running
  that reverse migration *does* cleanly drop them — but with no built-in
  export step first, that's permanent deletion of however many years of
  approval history existed. Export anything worth keeping before reverse-
  migrating, not after.
- The auto-managed `is_approver_sync` `ObjectPermission` row, plus any
  `bypass_approval` grants an admin made by hand, aren't tied to
  this plugin's migrations either — they're ordinary NetBox permission rows
  and stay in **Admin → Permissions** until someone deletes them manually.

None of this is unique to bad design so much as an inherent consequence of
"every change, forever, for audit purposes" — the fix for the parts that are
genuinely a problem (retention policy, a clean uninstall/export path) is future
work, not something this version does.

## Migrations

`migrations/0001_initial.py` was **hand-authored** — there was no running
NetBox instance available while writing this plugin to run `makemigrations`
against. The field definitions were cross-checked against real NetBox 4.2
migrations for other `NetBoxModel`-based models (field types, `BigAutoField`
PKs, the exact `TaggableManager`/`custom_field_data` boilerplate every
`NetBoxModel` contributes), but this is not a substitute for actually running
it. **Before relying on this migration**, from a real NetBox install with this
plugin installed:

```bash
python manage.py makemigrations netbox_change_approval --check --dry-run
```

If it reports drift, run `makemigrations` for real and replace
`0001_initial.py` (this is still your *first* migration — the app has never
been deployed yet, so replacing it outright is correct, not a schema change
against existing data).

## Running tests

From within a NetBox checkout with this plugin installed in editable mode:

```bash
python manage.py test netbox_change_approval
```

`tests/utils.py` has two small helpers (`fake_request`, `grant_bypass`) used
throughout — `fake_request` wraps NetBox's own `NetBoxFakeRequest` (the same
fake-request object NetBox's own test suite uses for `CustomValidator` tests),
and `grant_bypass` creates an `ObjectPermission` exactly the way an operator
would via the UI.

One thing to know if you add tests that call `serialization.apply_update` /
`apply_delete` directly (there is no `apply_create` — see "Create is
different" above, a create's data is already live by the time anything would
call it): those functions call `full_clean()`/`.delete()` themselves, which
re-enters the gate. In production they're only ever called from
`ChangeRequest.apply()`, which wraps them in `context.applying_change_on()`
for exactly that reason — do the same in any test that calls them standalone,
or you'll recursively queue a second pending change instead of actually
applying the first one.

## CI

`.github/workflows/test.yml` checks out a pinned NetBox tag alongside this
plugin, installs both, patches a `configuration.py` via a small inline script,
runs migrations, checks for migration drift (see "Migrations" above), and runs
the test suite. It was written against the documented NetBox plugin-testing
approach but **has not been run** (no NetBox instance was available while
writing this plugin — see the top of this document). Likely first-run
friction: the `configuration_example.py` string-replace for `DATABASE` is
version-sensitive; if NetBox's example config format has changed, replace that
step with writing a complete `configuration.py` from scratch instead of
patching the example. Treat the workflow as a strong starting point, not a
guarantee.

## Roadmap / known gaps

- **Queuing REST API bulk writes.** API writes are gated (NetBox's
  `ValidatedModelSerializer.validate()` calls `full_clean()`, so the validator
  runs; deletes hit `pre_delete` as usual). The middleware answers API requests
  with JSON (202/409) instead of a redirect. API creates validate a throwaway
  copy of the object, so the pending create is handed over on the request
  (`gating.stash_pending_create`). Bulk API update/delete runs in one
  transaction that NetBox rolls back when any object fails, which would take
  queued requests with it, so those objects are refused instead
  (`BatchCollector.REFUSE`). Queuing them would need per-object savepoints in
  NetBox's `perform_bulk_update`/`perform_bulk_destroy`.
- **Resubmit-after-reject.** Currently out of scope (see README limitations).
  If built, it should reuse the existing `ChangeRequest` row (new status,
  e.g. `changes_requested`) rather than adding a parallel concept.
- **List-view pending-change indicators** (e.g. a column in the Interfaces
  table). NetBox doesn't have a clean generic "inject a column into every
  model's table" plugin hook; the per-object banner and the dedicated queue
  cover the same need today.
