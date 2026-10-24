"""
A reason on every change request: NetBox's own "Changelog message" box (on
every add, edit and delete form, and `changelog_message` in the REST API)
becomes the request's reason. Approvers see it on the request, in the inbox,
in notifications and in webhook data.

An approval rule can require one ("Reason required"): a change that needs
approval under that rule is refused until the box is filled in, e.g. with a
ticket number.
"""
from django.utils.translation import gettext as _

#: The form field and API key NetBox uses for the changelog message.
FIELD = 'changelog_message'


def from_request(request, instance=None):
    """The reason typed for this change: the instance's changelog message if
    NetBox already set it, else the form or API data."""
    message = getattr(instance, '_changelog_message', None) if instance is not None else None
    if message:
        return str(message)[:200]
    if request is None:
        return ''
    for source in ('POST', 'data'):
        try:
            data = getattr(request, source, None)
            value = data.get(FIELD) if data is not None and hasattr(data, 'get') else None
        except Exception:
            value = None
        if value:
            return str(value).strip()[:200]
    return ''


def required_message():
    return _('This change needs approval and the approval rule asks for a reason: '
             'fill in the changelog message (for example a ticket number).')
