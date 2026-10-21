import django_tables2 as tables
from django.utils.translation import gettext_lazy as _

from netbox.tables import NetBoxTable, columns

from .models import ApprovalRoutingRule, ApprovalRule, AutoApprovalRule, ChangeRequest, ExemptModel


class ApprovalRuleTable(NetBoxTable):
    priority = tables.Column(linkify=True)
    description = tables.Column(linkify=True)
    content_type = columns.ContentTypeColumn(verbose_name=_('Object type'))
    action = columns.ChoiceFieldColumn()
    outcome = columns.ChoiceFieldColumn()
    approver_group = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = ApprovalRule
        fields = (
            'pk', 'id', 'priority', 'description', 'content_type', 'constraint', 'action',
            'outcome', 'approver_group', 'enabled', 'created', 'last_updated',
        )
        default_columns = (
            'pk', 'priority', 'description', 'content_type', 'constraint', 'action',
            'outcome', 'approver_group', 'enabled',
        )


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
#: and our own buttons instead, shown only while still pending and only to who
#: can use them (same rules the views enforce): Approve/Reject to superusers and
#: members of the request's approver group, Retract to the submitter.
_DECISION_BUTTONS = """
{% if record.status == "pending" %}
{% if request.user.is_superuser or record.approver_group and record.approver_group in request.user.groups.all %}
<a href="{% url 'plugins:netbox_change_approval:changerequest_approve' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-success" title="Approve"><i class="mdi mdi-check"></i></a>
<a href="{% url 'plugins:netbox_change_approval:changerequest_reject' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-danger" title="Reject"><i class="mdi mdi-close"></i></a>
{% endif %}
{% if record.submitted_by_id == request.user.pk %}
<a href="{% url 'plugins:netbox_change_approval:changerequest_retract' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-secondary" title="Retract"><i class="mdi mdi-undo"></i></a>
{% endif %}
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
