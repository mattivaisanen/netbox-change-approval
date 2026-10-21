import django_tables2 as tables
from django.utils.translation import gettext_lazy as _

from netbox.tables import NetBoxTable, columns

from .models import ApprovalRoutingRule, AutoApprovalRule, ChangeRequest, ExemptModel


class ExemptModelTable(NetBoxTable):
    content_type = columns.ContentTypeColumn(verbose_name=_('Object type'), linkify=True)
    enabled = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = ExemptModel
        fields = ('pk', 'id', 'content_type', 'constraint', 'description', 'enabled', 'created', 'last_updated')
        default_columns = ('pk', 'content_type', 'constraint', 'description', 'enabled')


class ApprovalRoutingRuleTable(NetBoxTable):
    description = tables.Column(linkify=True)
    content_type = columns.ContentTypeColumn(verbose_name=_('Object type'))
    approver_group = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = ApprovalRoutingRule
        fields = (
            'pk', 'id', 'priority', 'description', 'content_type', 'constraint',
            'approver_group', 'enabled', 'created', 'last_updated',
        )
        default_columns = ('pk', 'priority', 'description', 'content_type', 'approver_group', 'enabled')


class AutoApprovalRuleTable(NetBoxTable):
    description = tables.Column(linkify=True)
    content_type = columns.ContentTypeColumn(verbose_name=_('Object type'))
    action = columns.ChoiceFieldColumn()
    enabled = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = AutoApprovalRule
        fields = (
            'pk', 'id', 'priority', 'description', 'content_type', 'constraint',
            'action', 'enabled', 'created', 'last_updated',
        )
        default_columns = ('pk', 'priority', 'description', 'content_type', 'action', 'enabled')


#: ChangeRequest has no 'edit' view by design (it's a record of what happened, not
#: something to hand-edit) — NetBoxTable's default ActionsColumn reverses an edit/
#: delete URL unconditionally for any user with the relevant permission (including
#: superusers), regardless of whether one is actually registered, which raises
#: NoReverseMatch rather than just hiding the button. So: no 'edit'/'delete' here,
#: and our own approve/reject/retract buttons instead, shown only while still
#: pending. Same as approve/reject, retract is shown unconditionally for any
#: pending row rather than only on "My Requests" — the view itself checks
#: "is this your own request" and redirects with an error if not, the same
#: deferred-permission pattern already used for approve/reject.
_DECISION_BUTTONS = """
{% if record.status == "pending" %}
<a href="{% url 'plugins:netbox_change_approval:changerequest_approve' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-success" title="Approve"><i class="mdi mdi-check"></i></a>
<a href="{% url 'plugins:netbox_change_approval:changerequest_reject' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-danger" title="Reject"><i class="mdi mdi-close"></i></a>
<a href="{% url 'plugins:netbox_change_approval:changerequest_retract' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-secondary" title="Retract"><i class="mdi mdi-undo"></i></a>
{% endif %}
"""


class ChangeRequestTable(NetBoxTable):
    actions = columns.ActionsColumn(actions=('changelog',), extra_buttons=_DECISION_BUTTONS)
    id = tables.Column(linkify=True, verbose_name=_('Request'))
    content_type = columns.ContentTypeColumn(verbose_name=_('Object type'))
    object_repr = tables.Column(verbose_name=_('Object'))
    action = columns.ChoiceFieldColumn()
    status = columns.ChoiceFieldColumn()
    auto_approved = columns.BooleanColumn()
    submitted_by = tables.Column(linkify=True)
    approver_group = tables.Column(linkify=True)
    decided_by = tables.Column(linkify=True)
    batch_id = tables.Column(verbose_name=_('Batch'))

    class Meta(NetBoxTable.Meta):
        model = ChangeRequest
        fields = (
            'pk', 'id', 'content_type', 'object_repr', 'action', 'status', 'auto_approved',
            'submitted_by', 'submitted_at', 'approver_group', 'decided_by', 'decided_at',
            'decision_comment', 'applied_at', 'batch_id',
        )
        default_columns = (
            'id', 'content_type', 'object_repr', 'action', 'status', 'submitted_by',
            'submitted_at', 'approver_group',
        )
