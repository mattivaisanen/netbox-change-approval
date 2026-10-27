"""
The webhook `message` text (data.message), made from a template superusers
can change on Change Approval → Settings ("Webhook message").

The template is Jinja2, like NetBox's own webhook body templates, rendered
in a sandbox. Variables:

- by, by_email: who did it (empty when nobody did, e.g. an expired request)
- title: what happened ("Change request rejected")
- action, object_type, object: "Update", "device", "sw1"
- comment: the decision comment or the new comment, if any
- link: the request's address (full when netbox_url is set)
- id, status, reason, submitted_by, submitted_by_email, approver_group

Teams shows a single line break as a space, so the default puts a blank line
between lines.

No Django models are imported here: the plugin config (__init__.py) reads
DEFAULT at startup.
"""

DEFAULT = (
    'You have a notification in NetBox:\n\n'
    '{% if by %}From: {{ by }}\n\n{% endif %}'
    '{{ title }}:\n\n'
    '{{ action }} {{ object_type }} {{ object }}\n\n'
    '{% if comment %}Comment: {{ comment }}\n\n{% endif %}'
    '<p><a href="{{ link }}">Open in NetBox</a></p>'
)

SAMPLE = {
    'by': 'Bob Smith', 'by_email': 'bob@example.com', 'title': 'Change request rejected',
    'action': 'Update', 'object_type': 'device', 'object': 'sw1', 'comment': 'wrong rack',
    'link': 'https://netbox.example.com/plugins/change-approval/change-requests/12/', 'id': 12,
    'status': 'rejected', 'reason': 'Move to rack R2', 'submitted_by': 'Alice Admin',
    'submitted_by_email': 'alice@example.com', 'approver_group': 'Change Approvers',
}


def _environment():
    from jinja2.sandbox import SandboxedEnvironment

    return SandboxedEnvironment(autoescape=False)


def clean(text):
    """The template as stored: browser line breaks (\\r\\n) made plain."""
    return (text or '').replace('\r\n', '\n').replace('\r', '\n')


def check(text):
    """Raise ValueError with a readable reason if `text` can't be rendered."""
    from jinja2 import TemplateError

    try:
        _environment().from_string(clean(text)).render(**SAMPLE)
    except TemplateError as e:
        raise ValueError(str(e)) from e


def render(text, context):
    """`text` rendered with `context`; the default template if `text` is
    empty or broken (a broken one is logged)."""
    import logging

    from jinja2 import TemplateError

    if text:
        try:
            return _environment().from_string(clean(text)).render(**context).strip()
        except TemplateError:
            logging.getLogger('netbox_change_approval.notifications').exception(
                'Webhook message template failed; using the default',
            )
    return _environment().from_string(DEFAULT).render(**context).strip()
