"""
One-line titles for change requests in lists (Approval Inbox, dashboard
widget): what is being done to what kind of object, e.g. "Create Rack
Example rack 1" or "Edit Device Access switch sw1" (devices and virtual
machines include their role). Site, rack and the field changes are in the
quick view.

attach_titles() sets `nbca_title` on a list of requests with a fixed number
of queries, however long the list.
"""
from collections import defaultdict

from django.utils.translation import gettext as _

#: Object types whose role is part of the title.
ROLE_MODELS = {('dcim', 'device'), ('virtualization', 'virtualmachine')}


def _verb(change_request):
    from .models import ActionChoices

    return {
        ActionChoices.CREATE: _('Create'),
        ActionChoices.UPDATE: _('Edit'),
        ActionChoices.DELETE: _('Delete'),
    }.get(change_request.action, change_request.get_action_display())


def _roles(change_requests):
    """{change request pk: role name} for devices and VMs: the object's
    current role, or the role recorded in the request when the object is
    gone."""
    by_type = defaultdict(list)
    for cr in change_requests:
        ct = cr.content_type
        if (ct.app_label, ct.model) in ROLE_MODELS:
            by_type[ct].append(cr)

    roles = {}
    for ct, items in by_type.items():
        model = ct.model_class()
        if model is None:
            continue
        current = dict(
            model.objects.filter(pk__in=[cr.object_id for cr in items if cr.object_id])
            .values_list('pk', 'role__name')
        )
        recorded = {}
        missing = [cr for cr in items if not current.get(cr.object_id)]
        role_ids = {_recorded_role_id(cr) for cr in missing} - {None}
        if role_ids:
            role_model = model._meta.get_field('role').related_model
            recorded = dict(role_model.objects.filter(pk__in=role_ids).values_list('pk', 'name'))
        for cr in items:
            name = current.get(cr.object_id) or recorded.get(_recorded_role_id(cr))
            if name:
                roles[cr.pk] = name
    return roles


def _recorded_role_id(change_request):
    diff = change_request.diff if isinstance(change_request.diff, dict) else {}
    value = diff.get('role_id')
    if isinstance(value, dict):  # an update: {'old': ..., 'new': ...}
        value = value.get('new') or value.get('old')
    return value if isinstance(value, int) else None


def title(change_request, role=None):
    parts = [str(_verb(change_request)), str(change_request.content_type.name).capitalize()]
    if role:
        parts.append(role)
    parts.append(change_request.object_repr)
    return ' '.join(parts)


def attach_titles(change_requests):
    change_requests = list(change_requests)
    roles = _roles(change_requests)
    for cr in change_requests:
        cr.nbca_title = title(cr, roles.get(cr.pk))
    return change_requests
