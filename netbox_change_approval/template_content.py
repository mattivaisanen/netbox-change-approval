from django.utils.translation import gettext as _

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

        from . import pending_marks

        return self.render('netbox_change_approval/banner.html', {
            'change_request': change_request,
            'by_action': pending_marks.by_action(),
        })

    def list_buttons(self):
        """
        A clickable "N pending approvals" count above a model's list view.
        There's no generic per-row list-view hook in
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
        pending = ChangeRequest.objects.filter(content_type=content_type, status=StatusChoices.PENDING)
        count = pending.count()
        if not count:
            return ''

        from django.http import QueryDict

        from . import pending_marks
        from .models import ActionChoices

        # "2 new · 1 edit · 1 delete", each linking to this same list showing
        # just those objects (or to the requests, if there are too many to
        # put in a link).
        kinds = []
        if pending_marks.by_action():
            for action, label in (
                (ActionChoices.CREATE, _('new')), (ActionChoices.UPDATE, _('edit')), (ActionChoices.DELETE, _('delete')),
            ):
                ids = list(pending.filter(action=action, object_id__isnull=False).values_list('object_id', flat=True)[:101])
                if not ids:
                    continue
                if len(ids) <= 100:
                    query = QueryDict(mutable=True)
                    query.setlist('id', [str(pk) for pk in ids])
                    url = f'{self.context["request"].path}?{query.urlencode()}'
                else:
                    url = None
                kinds.append({'action': action, 'label': label, 'count': len(ids) if len(ids) <= 100 else '100+', 'url': url})

        return self.render('netbox_change_approval/list_pending_badge.html', {
            'count': count,
            'content_type_label': model_label(model),
            'kinds': kinds,
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


class InboxNavButton(PluginTemplateExtension):
    """
    "Approval Inbox" button with a count, at the top of the sidebar on every
    page (see inbox.py). models=None and head() for the same reason as
    PendingRowStyles above: it's the only hook that renders on every page.
    """
    def head(self):
        request = self.context.get('request')
        if request is None or not request.user.is_authenticated:
            return ''
        from django.urls import reverse

        from .inbox import inbox_count
        inbox_url = reverse('plugins:netbox_change_approval:changerequest_inbox')
        return self.render('netbox_change_approval/inbox_nav.html', {
            'count': inbox_count(request.user),
            'active': request.path == inbox_url,
        })


# There used to be an ApprovalTicker here (models=None, PluginTemplateExtension.
# navbar()) putting a pending-count badge in the top-right navbar icon tray. Moved:
# the count now lives in the Approvals page's own title (views.ApprovalsQueueView,
# templates/changerequest_approvals.html) instead — see DEVELOPER.md "Why the
# ticker moved to the Approvals page instead of the nav tab" for why it couldn't
# just move onto the new top-level nav tab itself (NetBox's plugin API has no hook
# for per-request-dynamic content inside the sidebar/nav tree, only the top navbar,
# an object list's buttons, and an object detail page's various regions).
template_extensions = [PendingChangeBanner, PendingRowStyles, InboxNavButton]
