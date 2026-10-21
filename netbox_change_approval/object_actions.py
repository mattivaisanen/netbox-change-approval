"""Bulk Approve / Reject buttons under the Approvals list (see
views.ChangeRequestBulkDecisionView)."""
from django.utils.translation import gettext_lazy as _

from netbox.object_actions import ObjectAction


class BulkApprove(ObjectAction):
    name = 'bulk_approve'
    label = _('Approve Selected')
    multi = True
    template_name = 'netbox_change_approval/buttons/bulk_approve.html'


class BulkReject(ObjectAction):
    name = 'bulk_reject'
    label = _('Reject Selected')
    multi = True
    template_name = 'netbox_change_approval/buttons/bulk_reject.html'
