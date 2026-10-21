from core.models import ObjectType

from netbox.plugins import PluginTemplateExtension

from .discovery import iter_gated_models, model_label
from .models import ChangeRequest, StatusChoices


class PendingChangeBanner(PluginTemplateExtension):
    """
    Shows a banner at the top of a gated object's detail page when it has a pending
    ChangeRequest against it (requirement: pending changes must be visible directly
    on the affected object, not just in a separate queue). Covers every model
    discovery.py considers gated — computed once at import time since the set of
    gated models doesn't change without a process restart anyway.
    """
    models = [model_label(model) for model in iter_gated_models()]

    def alerts(self):
        obj = self.context.get('object')
        if obj is None or obj.pk is None:
            return ''

        content_type = ObjectType.objects.get_for_model(type(obj))
        change_request = ChangeRequest.objects.filter(
            content_type=content_type,
            object_id=obj.pk,
            status=StatusChoices.PENDING,
        ).first()
        if change_request is None:
            return ''

        from .permissions import visible_change_requests

        request = self.context.get('request')
        can_view = request is not None and visible_change_requests(
            request.user, ChangeRequest.objects.filter(pk=change_request.pk),
        ).exists()
        return self.render('netbox_change_approval/banner.html', {
            'change_request': change_request,
            'can_view': can_view,
        })

    def list_buttons(self):
        """
        The "Pending Approval" tag (tags.py) is visible in a model's list view —
        but only once a user has manually added the Tags column, since NetBox's
        own tables rarely include it in default_columns (confirmed for Manufacturer,
        and that's the norm, not the exception, across core tables). That defeats
        the whole point of tagging creates immediately (see README "Pending
        creates are visible immediately") if nobody sees it without digging into
        column settings first. There's no generic per-row list-view hook in
        PluginTemplateExtension to fix this properly (list_buttons() is the only
        list-view hook there is — see DEVELOPER.md "Why there's no per-row list
        indicator" for why this is a button-bar badge instead of row styling), so
        this adds a visible, clickable count of this model's own pending items
        right next to the Add/Import buttons — the "parent folder" view gets the
        same visibility the object's own page already had.

        The model class arrives under the `object` context key, not `model` —
        utilities/templatetags/plugins.py's plugin_list_buttons(context, model)
        calls _get_registered_content(model, 'list_buttons', context), which
        always stores whatever it's given as context['object'] regardless of
        hook type (it's the same context-building code path alerts()/buttons()
        use for a real instance). The PluginTemplateExtension base class
        docstring's own context list ("model - the type of object being viewed")
        doesn't match this NetBox version's actual implementation.
        """
        model = self.context.get('object')
        request = self.context.get('request')
        if model is None or request is None or not request.user.is_authenticated:
            return ''

        content_type = ObjectType.objects.get_for_model(model)
        count = ChangeRequest.objects.filter(
            content_type=content_type, status=StatusChoices.PENDING,
        ).count()
        if not count:
            return ''

        return self.render('netbox_change_approval/list_pending_badge.html', {
            'count': count,
            'content_type_label': model_label(model),
        })


class PendingRowStyles(PluginTemplateExtension):
    """
    A second, separate extension class — not a method added to
    PendingChangeBanner — because that one's `models` is a populated list
    (every gated model label), and head()/navbar() are looked up *only* under
    the `None`-keyed bucket regardless of what `models` says on any given class
    (see utilities/templatetags/plugins.py:_get_registered_content — plugin_head()
    always calls it with obj=None, and the model-specific bucket only gets
    consulted when `hasattr(obj, '_meta')`, which None never satisfies). This is
    the same gotcha that bit the old navbar ticker (see git history / README) —
    anything meant to render on *every* page needs models=None (the class
    default) on its own class, full stop.

    Injects the CSS table_styling.py's PENDING_ROW_CSS_CLASS relies on — kept
    here rather than a static file because there's nothing to collectstatic for
    a few lines of CSS tied one-to-one to that Python constant.
    """
    def head(self):
        return self.render('netbox_change_approval/pending_row_styles.html')


# There used to be an ApprovalTicker here (models=None, PluginTemplateExtension.
# navbar()) putting a pending-count badge in the top-right navbar icon tray. Moved:
# the count now lives in the Approvals page's own title (views.ApprovalsQueueView,
# templates/changerequest_approvals.html) instead — see DEVELOPER.md "Why the
# ticker moved to the Approvals page instead of the nav tab" for why it couldn't
# just move onto the new top-level nav tab itself (NetBox's plugin API has no hook
# for per-request-dynamic content inside the sidebar/nav tree, only the top navbar,
# an object list's buttons, and an object detail page's various regions).
template_extensions = [PendingChangeBanner, PendingRowStyles]
