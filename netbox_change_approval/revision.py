"""
Send back for revision.

An approver sends a pending request back with a comment (ChangeRequest.
send_back()). Its submitter then gets one "Revise" button that opens NetBox's
own add/edit/delete page for the object, prefilled with what they submitted,
with `?nbca_revise=<pk>` on the URL. NetBox's edit form posts back to the same
URL, so the parameter is still there when the save is gated. Whatever request
the gate then creates is folded into the sent-back one (adopt_sent_back(),
called from ChangeRequest.save()): same row and number, revision + 1, status
pending again.

Updates and deletes are also matched without the parameter (same submitter,
same object), so editing the object directly counts as resubmitting too.
"""
import json

from django.utils.translation import gettext as _

from netbox.context import current_request
from utilities.views import get_action_url

REVISE_PARAM = 'nbca_revise'


def _requested_pk():
    request = current_request.get()
    value = getattr(request, 'GET', {}).get(REVISE_PARAM) if request is not None else None
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def find_sent_back(change_request):
    """The sent-back request a new pending `change_request` revises, or None."""
    from .models import ActionChoices, ChangeRequest, StatusChoices

    if change_request.submitted_by_id is None:
        return None
    candidates = ChangeRequest.objects.filter(
        status=StatusChoices.RETURNED,
        submitted_by_id=change_request.submitted_by_id,
        content_type_id=change_request.content_type_id,
        action=change_request.action,
    )
    pk = _requested_pk()
    if pk is not None:
        match = candidates.filter(pk=pk).first()
        if match is not None:
            return match
    if change_request.action != ActionChoices.CREATE and change_request.object_id is not None:
        return candidates.filter(object_id=change_request.object_id).order_by('-decided_at').first()
    return None


def adopt_sent_back(change_request):
    """Turn the unsaved `change_request` into the next revision of the
    sent-back request it revises, if any. Called from ChangeRequest.save()."""
    previous = find_sent_back(change_request)
    if previous is None:
        return
    change_request.pk = previous.pk
    change_request._state.adding = False
    change_request.created = previous.created
    change_request.submitted_at = previous.submitted_at
    change_request.custom_field_data = previous.custom_field_data
    change_request.batch_id = previous.batch_id
    change_request.revision = previous.revision + 1
    change_request.sent_back_by_id = previous.decided_by_id
    change_request.sent_back_comment = previous.decision_comment
    change_request.decided_by = None
    change_request.decided_at = None
    change_request.decision_comment = ''


def _field_by_key(model, key):
    for field in model._meta.concrete_fields:
        if field.attname == key or field.name == key:
            return field
    for field in model._meta.many_to_many:
        if field.name == key:
            return field
    return None


def _form_value(value, nullable_bool=True):
    if isinstance(value, bool):
        if value:
            return 'true'
        # A plain checkbox treats any non-empty string as ticked.
        return 'false' if nullable_bool else ''
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)


def form_initial(model, values):
    """Diff values ({attname: value}) as query parameters NetBox's add/edit
    form takes as initial data ({field name: str or [str]})."""
    params = {}
    for key, value in values.items():
        if key == 'custom_field_data' and isinstance(value, dict):
            for name, cf_value in value.items():
                params[f'cf_{name}'] = _form_value(cf_value)
            continue
        field = _field_by_key(model, key)
        if field is None or not getattr(field, 'editable', True):
            continue
        nullable_bool = field.get_internal_type() != 'BooleanField' or field.null
        params[field.name] = _form_value(value, nullable_bool=nullable_bool)
    return params


def revise_url(change_request):
    """Where the submitter's "Revise" button goes, or None if there's nowhere
    to go (the object an update/delete was about is gone)."""
    from django.http import QueryDict

    from .models import ActionChoices

    model = change_request.content_type.model_class()
    if model is None:
        return None
    diff = change_request.diff if isinstance(change_request.diff, dict) else {}
    params = {}
    try:
        if change_request.action == ActionChoices.CREATE:
            url = get_action_url(model, action='add')
            params = form_initial(model, diff)
        else:
            if not model.objects.filter(pk=change_request.object_id).exists():
                return None
            action = 'edit' if change_request.action == ActionChoices.UPDATE else 'delete'
            url = get_action_url(model, action=action, kwargs={'pk': change_request.object_id})
            if action == 'edit':
                params = form_initial(model, {
                    key: change.get('new') for key, change in diff.items() if isinstance(change, dict)
                })
    except Exception:  # NoReverseMatch: model has no such view
        return None

    query = QueryDict(mutable=True)
    for key, value in params.items():
        if isinstance(value, list):
            query.setlist(key, value)
        else:
            query[key] = value
    query[REVISE_PARAM] = str(change_request.pk)
    query['return_url'] = change_request.get_absolute_url()
    return f'{url}?{query.urlencode()}'


def revise_message(change_request):
    """Shown on the form the "Revise" button opens."""
    who = change_request.decided_by or _('An approver')
    return _('Revising request #%(pk)s. %(who)s asked: "%(comment)s"') % {
        'pk': change_request.pk, 'who': who, 'comment': change_request.decision_comment,
    }
