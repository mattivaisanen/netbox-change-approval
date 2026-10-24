"""
Updating your own pending request.

While a request waits for approval its object is locked, but not for the
person who submitted it: they can open the object's normal edit page, change
it again and press "Update request" (the Save button, renamed). That updates
the same request: its changes are replaced, the revision goes up by one,
approvals so far start over, and the approvers are notified again. Everyone
else is still blocked.

- Pending edit: the edit page opens with the values the submitter asked for
  (not the current, unchanged ones), so they edit their request rather than
  start over. Saving stores the new difference from the current object; if
  nothing differs any more, the request is withdrawn.
- Pending new object: the object already exists (see gating.py), so the
  save goes through and the request's recorded values are updated.
- Pending delete: stays locked; retract it instead.

Web UI, one object at a time. Bulk edit, import and the REST API still treat
the object as locked.
"""
from django.contrib import messages
from django.http import HttpResponseRedirect, QueryDict
from django.utils.translation import gettext as _

from .models import ActionChoices, StatusChoices

#: On the edit page URL: "this form edits pending request <pk>".
AMEND_PARAM = 'nbca_amend'


def amendable(change_request, user, request=None):
    """Whether `user` may update this pending request by editing its object."""
    if change_request is None or change_request.status != StatusChoices.PENDING:
        return False
    if change_request.action not in (ActionChoices.UPDATE, ActionChoices.CREATE):
        return False
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if change_request.submitted_by_id != user.pk:
        return False
    if request is not None and _is_bulk_or_api(request):
        return False
    return True


def amending_here(change_request, request):
    """
    Whether this save comes from the "Update request" edit page for this
    request (our parameter is on the URL; the edit form posts back to it).

    For a pending edit the new values *replace* the request, so any other
    save of the object by the submitter (e.g. another object's form that also
    saves this one) must not count as an update: it would silently drop what
    the request asked for. Such saves stay blocked, as before. A pending new
    object records its full state either way, so nothing can be lost there.
    """
    if change_request.action != ActionChoices.UPDATE:
        return True
    params = getattr(request, 'GET', None)
    return params is not None and params.get(AMEND_PARAM) == str(change_request.pk)


def _is_bulk_or_api(request):
    from .context import batch_collector

    if batch_collector.get() is not None:
        return True
    try:
        from utilities.api import is_api_request
        return is_api_request(request)
    except Exception:
        return False


def amend(change_request, instance, request):
    """
    Called by gating._gate() instead of refusing the save, when the submitter
    edits the object of their own pending request.

    Returns what _gate() should return: the (updated, saved) request for a
    pending edit, so the save is stopped and the user lands back on the
    object; None for a pending create, so the save goes through.
    """
    from . import notifications, serialization
    from .approvals import apply_rule_settings
    from .gating import _first_matching_rule

    model = type(instance)
    if change_request.action == ActionChoices.UPDATE:
        current = model.objects.get(pk=instance.pk)
        diff = serialization.snapshot_update(current, instance)
        if not diff:
            change_request.retract(request.user)
            messages.info(request, _(
                'Request #%(pk)s withdrawn: your edit puts everything back as it is now, so there is '
                'nothing left to approve.'
            ) % {'pk': change_request.pk}, fail_silently=True)
            return None
    else:
        diff = serialization.snapshot_create(instance)

    previous_diff = change_request.diff
    change_request.diff = diff
    change_request.object_repr = str(instance)[:300]
    change_request.revision += 1
    from . import reasons
    reason = reasons.from_request(request, instance)
    if reason:
        change_request.reason = reason
    apply_rule_settings(change_request, _first_matching_rule(instance, change_request.action))
    change_request._nbca_amended = True
    change_request.save()
    notifications.request_updated(change_request)
    if change_request.action == ActionChoices.UPDATE:
        # Files uploaded for the previous version and not kept by this one.
        from .serialization import discard_held_uploads, held_upload_names
        keep = {name for _field, name in held_upload_names(type(instance), diff)}
        discard_held_uploads(change_request.content_type, change_request.object_id, previous_diff, keep=keep)

    if change_request.action == ActionChoices.CREATE:
        messages.success(request, _(
            'Request #%(pk)s updated (revision %(rev)s) and sent to the approvers again.'
        ) % {'pk': change_request.pk, 'rev': change_request.revision}, fail_silently=True)
        request._nbca_suppress_default_create_message = True
        return None
    return change_request


def edit_url(change_request):
    """The object's edit page, filled in with what this pending request asks
    for (the submitter's "Update request" link), or None."""
    from utilities.views import get_action_url

    from .revision import form_initial

    model = change_request.content_type.model_class()
    if model is None or change_request.object_id is None:
        return None
    try:
        url = get_action_url(model, action='edit', kwargs={'pk': change_request.object_id})
    except Exception:
        return None
    query = QueryDict(mutable=True)
    if change_request.action == ActionChoices.UPDATE:
        diff = change_request.diff if isinstance(change_request.diff, dict) else {}
        params = form_initial(model, {
            key: change.get('new') for key, change in diff.items() if isinstance(change, dict)
        })
        for key, value in params.items():
            if isinstance(value, list):
                query.setlist(key, value)
            else:
                query[key] = value
    query[AMEND_PARAM] = str(change_request.pk)
    query['return_url'] = change_request.target_object.get_absolute_url() if change_request.target_object else ''
    return f'{url}?{query.urlencode()}'


def _edit_view_object(request, match):
    """(model, pk) when `match` is a gated object's edit page, else None."""
    from netbox.views.generic import ObjectEditView

    from .discovery import is_gated_model

    view_class = getattr(match.func, 'view_class', None)
    if view_class is None or not issubclass(view_class, ObjectEditView):
        return None
    pk = match.kwargs.get('pk')
    queryset = getattr(view_class, 'queryset', None)
    if not pk or queryset is None or not is_gated_model(queryset.model):
        return None
    return queryset.model, pk


def own_pending_for_edit_page(request):
    """The current user's own amendable pending request for the object whose
    edit page this request is for, or None."""
    from django.urls import Resolver404, resolve

    from core.models import ObjectType

    from .models import ChangeRequest

    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    found = _edit_view_object(request, match)
    if found is None:
        return None
    model, pk = found
    change_request = ChangeRequest.objects.filter(
        content_type=ObjectType.objects.get_for_model(model), object_id=pk, status=StatusChoices.PENDING,
    ).first()
    return change_request if amendable(change_request, user) else None


class AmendMiddleware:
    """
    Opening the edit page of your own pending edit without our parameter
    (e.g. from the object's Edit button) redirects once to the same page
    filled in with the values you asked for. Otherwise the form would show
    the current, unchanged values and saving it would quietly drop your
    request's changes.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method != 'GET' or AMEND_PARAM in request.GET:
            return None
        try:
            change_request = own_pending_for_edit_page(request)
        except Exception:
            return None
        if change_request is None or change_request.action != ActionChoices.UPDATE:
            return None
        url = edit_url(change_request)
        return HttpResponseRedirect(url) if url else None
