"""
A "Change Requests" tab on the page of every gated object: its approval
history (pending, applied, rejected, ...), newest first. Shown only when the
object has any. The badge counts them all; the list shows the ones the user
may see (permissions.visible_change_requests).
"""
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from netbox.views import generic
from utilities.views import ViewTab, register_model_view

from .discovery import iter_gated_models
from .models import ChangeRequest
from .tables import ChangeRequestTable


def _requests_for(obj):
    return ChangeRequest.objects.filter(
        content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk,
    )


class ObjectChangeRequestsView(generic.ObjectChildrenView):
    child_model = ChangeRequest
    table = ChangeRequestTable
    actions = ()
    tab = ViewTab(
        label=_('Change Requests'),
        badge=lambda obj: _requests_for(obj).count(),
        hide_if_empty=True,
        weight=9900,  # just before Changelog
    )

    def get_children(self, request, parent):
        from .permissions import visible_change_requests

        return visible_change_requests(request.user, _requests_for(parent)).select_related(
            'content_type', 'submitted_by', 'approver_group', 'decided_by',
        )


def register():
    """One view per gated model; must run before NetBox builds its URLs."""
    for model in iter_gated_models():
        view = type(f'{model.__name__}ChangeRequestsView', (ObjectChangeRequestsView,), {
            'queryset': model.objects.all(),
        })
        register_model_view(model, 'change_requests', path='change-requests')(view)
