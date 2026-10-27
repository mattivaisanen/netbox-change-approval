# Sending change request notifications by e-mail, Teams or anything else

NetBox shows this plugin's notifications in its own bell. To also send them
by e-mail, as a Teams message or to any other service, use NetBox's
**Event Rules** with a **Webhook**. The plugin puts everything a message
needs in the event data, including **who should get it** and their e-mail
addresses, so the receiving service only has to deliver it.

The examples use Microsoft Power Automate. Any service that accepts an HTTP
POST works the same way (Slack, a mail relay, n8n, Zapier, a small script of
your own).

## 1. Tell the plugin your NetBox address (optional)

So links in messages are complete, set it in `configuration.py`:

```python
PLUGINS_CONFIG = {
    'netbox_change_approval': {
        'netbox_url': 'https://netbox.example.com',
    },
}
```

Without it, links are paths like `/plugins/change-approval/change-requests/12/`.

## 2. Pick the events

Two ways, both on **Operations → Event Rules → Add**, object type
**Change Approval > Change Request**:

- **The plugin's own event types (recommended):** one per case, fired
  together with the bell notification, with the message text ready:

  | Event type | Sent to (`recipients`) |
  |---|---|
  | Change request needs your approval | the approver group (new or resubmitted request) |
  | New comment on change request | the submitter and the approver group, except the author |
  | Change request approved | the submitter |
  | Change request approved, needs more approvers | the submitter |
  | Change request rejected | the submitter |
  | Change request sent back to you | the submitter |
  | Change request expired | the submitter |
  | Change request retracted | the approver group, when the submitter takes back a waiting request (also "retract and edit") |

- **NetBox's "Object created" / "Object updated":** fire on every change to
  a request. Their data is the change request itself, with `recipients`
  worked out from its status (the approver group while it waits, the
  submitter once it's decided, the approver group again if the submitter
  retracts it). A new comment fires "Object updated" too, with `recipients`
  being the submitter and the approver group (except the author) and the
  comment in `last_comment`. Use these if you already have such a rule; they
  carry no ready message text.

## 3. What the data contains

Fields your template can use as `{{ data.<field> }}`:

| Field | Plugin events | Object created/updated | Example |
|---|---|---|---|
| `message` | ✓ | ✓ | ready-to-send text, see below |
| `id` | ✓ | ✓ | `12` |
| `display` | ✓ | ✓ | `#12 Update device sw1` |
| `detail` | ✓ | | `rejected by bob: wrong rack` |
| `event` | ✓ | | `Change request rejected` |
| `link` | ✓ | ✓ | `https://netbox.example.com/plugins/change-approval/change-requests/12/` |
| `action`, `status`, `revision` | ✓ | ✓ | `update`, `rejected`, `1` |
| `object_type`, `object` | ✓ | `object_type`, `object_repr` | `dcim.device`, `sw1` |
| `changes` | ✓ | `diff` | `[{"field": "Rack", "old": "R1", "new": "R2"}]` |
| `submitted_by`, `by` | ✓ | `submitted_by`, `decided_by` | `{"username": "alice", "name": "Alice", "email": "alice@example.com"}` |
| `comment` | ✓ | `decision_comment` | `wrong rack` |
| `approver_group`, `approvers` | ✓ | `approver_group` | `Change Approvers` |
| `recipients` | ✓ | ✓ | `[{"username": "alice", "email": "alice@example.com", ...}]` |
| `recipient_emails` | ✓ | ✓ | `alice@example.com;bob@example.com` |
| `revision_changes` | ✓ | ✓ | after a resubmit: what changed since the revision sent back, `[{"field": "Rack", "old": "R2", "new": "R3"}]` |
| `last_comment` | `comment` | ✓ | `{"author": {"username": "bob", "email": "bob@example.com", ...}, "text": "why this VLAN?", "created": "..."}` |

People with no e-mail address in NetBox are listed in `recipients` but left
out of `recipient_emails`. Set e-mail addresses under **Admin → Users**.

**Who is who:** `recipient_emails` (and `recipients`) are always the people
the message is *for*. `by`, `submitted_by` and `decided_by` (each with an
`email`) are who did something; use them for a "From" or "Reply to".

`message` is a short plain-text notice you can send as is (line breaks as
`\n`), so people know to look in NetBox. It names who did it, what
happened, the request with its object type, and the comment if there is
one. It leaves out the changes; those are in `changes` and
`revision_changes`. For a decision:

```text
You have a notification in NetBox:

From: Bob Smith

Change request rejected:

Update device sw1

Comment: wrong rack

<p><a href="https://netbox.example.com/plugins/change-approval/change-requests/12/">Open in NetBox</a></p>
```

Lines are separated by a blank line (`\n\n`), because Teams shows a single
line break as a space.

The last line is an HTML link, so it shows as "Open in NetBox" in an HTML
e-mail or Teams message. The plain address is in `link`.

On "Object updated" the title follows the status ("New change request",
"Change request updated", "Change request sent back", ...). An expired
request has no "From" line.

### Changing the message

Superusers change the text on **Change Approval → Settings → Webhook
message** (or `webhook_message` in `configuration.py`). It is a Jinja2
template, like NetBox's webhook body templates. Leave it empty for the
default:

```jinja
You have a notification in NetBox:

{% if by %}From: {{ by }}

{% endif %}{{ title }}:

{{ action }} {{ object_type }} {{ object }}

{% if comment %}Comment: {{ comment }}

{% endif %}<p><a href="{{ link }}">Open in NetBox</a></p>
```

| Variable | Example |
|---|---|
| `by`, `by_email` | `Bob Smith`, `bob@example.com` (empty when nobody acted) |
| `title` | `Change request rejected` |
| `action`, `object_type`, `object` | `Update`, `device`, `sw1` |
| `comment` | `wrong rack` |
| `link` | `https://netbox.example.com/plugins/change-approval/change-requests/12/` |
| `id`, `status`, `reason` | `12`, `rejected`, `Move to rack R2` |
| `submitted_by`, `submitted_by_email` | `Alice Admin`, `alice@example.com` |
| `approver_group` | `Change Approvers` |

A template with an error can't be saved; the page says what is wrong.

## 4. The webhook

**Operations → Webhooks → Add**:

- **Name**: e.g. `Change approval messages`
- **URL**: the address your service gives you (for Power Automate: the URL
  of the "When an HTTP request is received" trigger, see below)
- **HTTP method**: `POST`, **content type**: `application/json`
- **Body template**: a small JSON document (Jinja2), the same for every
  service below:

```jinja
{
  "event": {{ data.event | default(event, true) | tojson }},
  "title": {{ data.display | tojson }},
  "detail": {{ data.detail | default('', true) | tojson }},
  "link": {{ data.link | tojson }},
  "status": {{ data.status | tojson }},
  "recipients": {{ data.recipients | tojson }},
  "recipient_emails": {{ data.recipient_emails | tojson }}
}
```

Then the Event Rule: object type Change Request, the event types from
step 2, **action type** Webhook, **webhook** the one above. Add a
condition if you want to skip something, for example only rejections:
`{"attr": "status", "value": "rejected"}`.

Test it: submit a change that needs approval, then look at the webhook's
receiver (or NetBox's background worker log if nothing arrives).

## Example: e-mail with Power Automate (Outlook)

1. New **Instant cloud flow** → trigger **When an HTTP request is received**.
   Paste this as the request body JSON schema:

   ```json
   {
     "type": "object",
     "properties": {
       "event": {"type": "string"},
       "title": {"type": "string"},
       "detail": {"type": "string"},
       "link": {"type": "string"},
       "status": {"type": "string"},
       "recipient_emails": {"type": "string"},
       "recipients": {"type": "array", "items": {"type": "object", "properties": {
         "username": {"type": "string"}, "name": {"type": "string"}, "email": {"type": "string"}
       }}}
     }
   }
   ```

2. Add **Condition**: `recipient_emails` is not equal to (empty), so nothing
   is sent when nobody has an address.
3. In "If yes", add **Send an email (V2)** (Office 365 Outlook):
   - **To**: `recipient_emails` (semicolon-separated, as Outlook expects)
   - **Subject**: `event`: `title`
   - **Body**: `detail`, then a link to `link`
4. Save. Copy the trigger's **HTTP URL** into the NetBox webhook's URL.

The mail comes from the account that owns the flow; use a shared mailbox or
service account for an "approval bot" sender (**Send an email from a shared
mailbox (V2)**).

## Example: Teams message to each person, from the Flow bot

Teams **Incoming Webhooks** (and Workflows' "post to a channel" webhooks)
can only post to a **channel**, not to one person. To message people
directly, let Power Automate's **Flow bot** do it:

1. Same trigger and schema as the e-mail example.
2. Add **Apply to each** over `recipients`.
3. Inside it, add **Post message in a chat or channel** (Microsoft Teams):
   - **Post as**: Flow bot
   - **Post in**: Chat with Flow bot
   - **Recipient**: `email` of the current item (their Teams sign-in
     address; usually the same as their e-mail)
   - **Message**: `event`: `title` — `detail` — `link`
4. Save and copy the trigger's URL into the webhook.

The message arrives in each person's chat with the Flow bot (shown in Teams
as "Workflows"). People without an e-mail address in NetBox are skipped,
because Teams needs it to find them; add a **Condition** on `email` inside
the loop to avoid failed runs.

To post to a team channel instead (everyone in the approver group sees it),
use **Post message in a chat or channel** with **Post in: Channel**, or a
channel's Workflows webhook, and leave out the loop.

## Example: any other service

The body above is plain JSON, so anything that takes a POST works: point
the webhook's URL at it and read the fields. For a service that needs a
different shape (Slack's `{"text": "..."}`, say), change the body template,
e.g.:

```jinja
{"text": {{ (data.event ~ ": " ~ data.display ~ " " ~ data.detail ~ " " ~ data.link) | tojson }}}
```

## Troubleshooting

- **Nothing arrives:** check the Event Rule is enabled and has the right
  object type and event types; check NetBox's background worker is running
  (webhooks are sent by it).
- **`recipient_emails` is empty:** the people concerned have no e-mail
  address in NetBox.
- **Links are only paths:** set `netbox_url` (step 1).
