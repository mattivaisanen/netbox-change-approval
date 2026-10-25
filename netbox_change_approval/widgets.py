"""
A bespoke dashboard widget, not a reliance on NetBox's generic ObjectListWidget/
ObjectCountsWidget pointed at ChangeRequest — those work (both are fully usable
against this plugin's models; confirmed live), but show every change request
unfiltered and have no inline decide action, which is the whole reason someone
would want this on their homepage in the first place: "approve right from
there" (see README "Task 9"). This one scopes to "routed to me, still pending"
and links straight to approve/reject.
"""
import hashlib
import random

from django.middleware.csrf import get_token
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


# Shown when nothing waits on the user: a random drawing from
# templates/netbox_change_approval/inc/idle/ and a random line, IDLE_LINES
# for approvers and REQUESTER_IDLE_LINES for everyone else.
IDLE_DRAWINGS = (
    'coffee', 'hammock', 'cat', 'duck', 'fishing',
    'night', 'plane', 'tumbleweed', 'checklist', 'beach',
    'snail', 'kite', 'balloon', 'plant', 'fishbowl',
    'rocket', 'campfire', 'clock', 'windmill', 'birds',
)
IDLE_LINES = (
    gettext_noop('Nothing awaiting approval. The racks are humming quietly.'),
    gettext_noop('Inbox zero. Even the patch cables are relaxing.'),
    gettext_noop('Not a single change request in sight.'),
    gettext_noop('The approval queue has taken the day off.'),
    gettext_noop('Quiet on the change front.'),
    gettext_noop('Every port in its happy place.'),
    gettext_noop('Nothing to sign off. The network is behaving itself.'),
    gettext_noop('All quiet. Even the LEDs are blinking slower.'),
    gettext_noop('Change window wide open, nobody climbing through.'),
    gettext_noop('The rubber stamp is gathering dust.'),
)
REQUESTER_IDLE_LINES = (
    gettext_noop('Nothing sent back. Clean changes all round.'),
    gettext_noop('No revisions needed. The approvers seem pleased.'),
    gettext_noop('Every request through. The network says thanks.'),
    gettext_noop('Nothing bounced back. Every cable stayed put.'),
    gettext_noop('No changes waiting on a fix.'),
    gettext_noop('All requests settled. A fine moment for coffee.'),
    gettext_noop('Nothing to revise. The servers are taking it easy too.'),
    gettext_noop('Paperwork done. Tumbleweed territory.'),
)


@register_widget
class ApprovalsWidget(DashboardWidget):
    default_title = _('Pending Approvals')
    description = _(
        'Change requests routed to you for approval, and your own requests sent back to you, '
        'rejected or expired, with quick links to act on each one.'
    )
    template_name = 'netbox_change_approval/dashboard_widget.html'
    width = 12
    height = 4

    def render(self, request):
        if not request.user.is_authenticated:
            return ''
        return self._render(request, self._items(request.user))

    def live(self, request, known_sig=None):
        """
        (signature, html) for the page's background refresh (views.LiveRefreshView).
        html is None when nothing in the widget changed since `known_sig`, so
        an unchanged widget isn't redrawn (and its idle drawing doesn't swap).
        """
        items = self._items(request.user)
        sig = self.signature(*items)
        if sig == known_sig:
            return sig, None
        return sig, self._render(request, items)

    @staticmethod
    def signature(rows, sent_back, closed):
        """Short fingerprint of what the widget shows: which requests (a batch
        is one row), whether you may decide them, which revision, and how many
        are in each batch."""
        parts = (
            [('p', item['lead'].pk, item['lead'].revision, can, len(item['members'])) for item, can in rows]
            + [('s', item['lead'].pk, item['lead'].revision, str(item['lead'].decided_at), len(item['members']))
               for item in sent_back]
            + [('c', item['lead'].pk, len(item['members'])) for item in closed]
        )
        return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]

    def _items(self, user):
        """(pending, sent back, rejected or expired), each a list of inbox
        items ({'lead', 'members'}): a bulk edit or change set is one row,
        as in the Approval Inbox, because deciding it decides the batch."""
        from .absence import decide_group_ids
        from .inbox import _decide, _rejected, _sent_back, group_items

        related = ('content_type', 'submitted_by', 'decided_by', 'approver_group')
        group_ids = decide_group_ids(user)
        rows = [
            (
                item,
                item['lead'].submitted_by_id != user.pk
                and (user.is_superuser or item['lead'].approver_group_id in group_ids),
            )
            for item in group_items(_decide(user).select_related(*related), limit=10)
        ]
        # Your own requests sent back to you (or retracted to edit), with Revise.
        sent_back = group_items(_sent_back(user).select_related(*related), limit=10)
        # Your rejected and expired requests not read yet, each with Mark as
        # read (opening the request marks it read too).
        closed = group_items(_rejected(user).select_related(*related), limit=10)
        return rows, sent_back, closed

    def _render(self, request, items):
        rows, sent_back, closed = items
        is_approver = request.user.has_perm(
            f'netbox_change_approval.{permissions.IS_APPROVER_ACTION}_changerequest'
        )
        idle_line = _(random.choice(IDLE_LINES if is_approver else REQUESTER_IDLE_LINES))
        # Field changes for each row's quick view (the arrow), as in the inbox.
        from .views import _diff_rows, prefetch_display_names

        # Every member of each batch (its quick view lists them all), and each
        # lead (a separate instance when it heads a batch).
        items_shown = [item for item, _can in rows] + sent_back + closed
        shown = [item['lead'] for item in items_shown] + [cr for item in items_shown for cr in item['members']]
        names = prefetch_display_names(shown)
        from .titles import attach_titles
        attach_titles(shown)
        for cr in shown:
            cr.quick_rows = _diff_rows(cr, names=names)
        return render_to_string(self.template_name, {
            'change_requests': rows, 'sent_back': sent_back, 'closed': closed, 'idle_line': idle_line,
            'live_sig': self.signature(rows, sent_back, closed),
            'csrf_token': get_token(request) if closed else '',
            'idle_drawing': f'netbox_change_approval/inc/idle/{random.choice(IDLE_DRAWINGS)}.html',
        })
