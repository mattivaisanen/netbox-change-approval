"""
A bespoke dashboard widget, not a reliance on NetBox's generic ObjectListWidget/
ObjectCountsWidget pointed at ChangeRequest — those work (both are fully usable
against this plugin's models; confirmed live), but show every change request
unfiltered and have no inline decide action, which is the whole reason someone
would want this on their homepage in the first place: "approve right from
there" (see README "Task 9"). This one scopes to "routed to me, still pending"
and links straight to approve/reject.
"""
import random

from django.template.loader import render_to_string
# Not gettext_lazy: DashboardWidget.__str__ returns `self.title` (derived from
# default_title) directly, with no str() wrapper — Python's __str__ dunder
# requires an actual str back, and a lazy proxy fails that with "__str__
# returned non-string (type __proxy__)" the moment anything calls str() on a
# widget instance. NetBox's own widgets (extras/dashboard/widgets.py) import
# the non-lazy gettext for exactly this reason — matching that here, not a
# general lazy-vs-eager style choice.
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from extras.dashboard.utils import register_widget
from extras.dashboard.widgets import DashboardWidget

from . import permissions
from .models import ChangeRequest, StatusChoices


# Shown, one at random per page load, when nothing waits on the user:
# IDLE_LINES to approvers, REQUESTER_IDLE_LINES to everyone else.
IDLE_LINES = (
    gettext_noop('Nothing waiting on you right now. The racks are humming quietly.'),
    gettext_noop('Inbox zero. Even the patch cables are relaxing.'),
    gettext_noop('All clear. Go on, refill your coffee.'),
    gettext_noop('No approvals pending. The change window is all yours.'),
    gettext_noop('Nothing to review. Every port is in its happy place.'),
    gettext_noop('Quiet on the change front. Enjoy it while it lasts.'),
    gettext_noop('All caught up. The servers send their regards.'),
    gettext_noop('Nothing for you here. Even the LEDs are blinking slower.'),
)
REQUESTER_IDLE_LINES = (
    gettext_noop('Nothing sent back to you. Your approvers must like your work.'),
    gettext_noop('All your requests are through. The network thanks you.'),
    gettext_noop('No revisions needed. Clean changes, happy approvers.'),
    gettext_noop('Nothing to fix right now. Time for a coffee.'),
    gettext_noop('Your queue is empty. The servers are taking it easy too.'),
    gettext_noop('No changes bounced back. Every cable stayed where you put it.'),
)


@register_widget
class ApprovalsWidget(DashboardWidget):
    default_title = _('Pending Approvals')
    description = _(
        'Change requests routed to you for approval, and your own requests sent back to you, '
        'with quick links to act on each one.'
    )
    template_name = 'netbox_change_approval/dashboard_widget.html'
    width = 12
    height = 4

    def render(self, request):
        if not request.user.is_authenticated:
            return ''
        is_approver = request.user.has_perm(
            f'netbox_change_approval.{permissions.IS_APPROVER_ACTION}_changerequest'
        )
        idle_line = _(random.choice(IDLE_LINES if is_approver else REQUESTER_IDLE_LINES))

        queryset = permissions.approver_queue(
            request.user, ChangeRequest.objects.filter(status=StatusChoices.PENDING),
        ).select_related('content_type', 'submitted_by', 'approver_group').order_by('-submitted_at')[:10]

        user = request.user
        from .absence import decide_group_ids
        group_ids = decide_group_ids(user)
        rows = [
            (
                cr,
                cr.submitted_by_id != user.pk
                and (user.is_superuser or cr.approver_group_id in group_ids),
            )
            for cr in queryset
        ]
        # Your own requests sent back to you (or retracted to edit), with Revise.
        sent_back = ChangeRequest.objects.filter(
            submitted_by=user, status=StatusChoices.RETURNED,
        ).select_related('content_type', 'submitted_by', 'approver_group').order_by('-decided_at')[:10]
        # Field changes for each row's quick view (the arrow), as in the inbox.
        from .views import _diff_rows, prefetch_display_names

        sent_back = list(sent_back)
        shown = [cr for cr, _can in rows] + sent_back
        names = prefetch_display_names(shown)
        from .titles import attach_titles
        attach_titles(shown)
        for cr in shown:
            cr.quick_rows = _diff_rows(cr, names=names)
        return render_to_string(self.template_name, {
            'change_requests': rows, 'sent_back': sent_back, 'idle_line': idle_line,
        })
