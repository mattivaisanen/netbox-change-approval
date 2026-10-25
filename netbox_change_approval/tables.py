import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from netbox.tables import NetBoxTable, columns

from .rule_access import RuleActionsColumn
from .models import ApprovalRule, ChangeRequest


class ApprovalRuleTable(NetBoxTable):
    actions = RuleActionsColumn()
    priority = tables.Column(linkify=True)
    description = tables.Column(linkify=True)
    content_type = columns.ContentTypeColumn(verbose_name=_('Object type'))
    action = columns.ChoiceFieldColumn()
    outcome = columns.ChoiceFieldColumn()
    approver_group = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()

    def render_priority(self, value, record):
        # The Default rule is always last, whatever its stored priority.
        if record.is_default:
            return format_html('<a href="{}">{}</a>', record.get_absolute_url(), _('Last'))
        return format_html('<a href="{}">{}</a>', record.get_absolute_url(), value)

    class Meta(NetBoxTable.Meta):
        model = ApprovalRule
        fields = (
            'pk', 'id', 'priority', 'description', 'content_type', 'constraint', 'action',
            'outcome', 'approver_group', 'required_approvals', 'require_reason', 'expire_after_days', 'enabled',
            'created', 'last_updated',
        )
        default_columns = (
            'pk', 'priority', 'description', 'content_type', 'constraint', 'action',
            'outcome', 'approver_group', 'enabled',
        )


#: ChangeRequest has no 'edit' view by design (it's a record of what happened, not
#: something to hand-edit) — NetBoxTable's default ActionsColumn reverses an edit/
#: delete URL unconditionally for any user with the relevant permission (including
#: superusers), regardless of whether one is actually registered, which raises
#: NoReverseMatch rather than just hiding the button. So: no 'edit'/'delete' here,
#: and our own buttons instead, shown only while still pending and only to who
#: can use them (same rules the views enforce): Approve/Reject to superusers and
#: members of the request's approver group, never on their own requests;
#: Retract to the submitter.
_DECISION_BUTTONS = """
{% if record.status == "pending" %}
{% if record.submitted_by_id != request.user.pk %}{% if record.nbca_can_decide %}
<a data-nbca-modal href="{% url 'plugins:netbox_change_approval:changerequest_approve' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-success" title="Approve"><i class="mdi mdi-check"></i></a>
<a data-nbca-modal href="{% url 'plugins:netbox_change_approval:changerequest_sendback' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-warning" title="Send back for revision"><i class="mdi mdi-undo-variant"></i></a>
<a data-nbca-modal href="{% url 'plugins:netbox_change_approval:changerequest_reject' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-danger" title="Reject"><i class="mdi mdi-close"></i></a>
{% endif %}{% endif %}
{% if record.submitted_by_id == request.user.pk %}
<a data-nbca-modal href="{% url 'plugins:netbox_change_approval:changerequest_retract' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-secondary" title="Retract"><i class="mdi mdi-undo"></i></a>
{% endif %}
{% elif record.status == "returned" and record.submitted_by_id == request.user.pk %}
<a href="{% url 'plugins:netbox_change_approval:changerequest_revise' pk=record.pk %}"
   class="btn btn-sm btn-primary" title="Revise and resubmit"><i class="mdi mdi-pencil"></i></a>
<a data-nbca-modal href="{% url 'plugins:netbox_change_approval:changerequest_discard' pk=record.pk %}?return_url={{ request.get_full_path|urlencode }}"
   class="btn btn-sm btn-primary" title="Discard"><i class="mdi mdi-delete-outline"></i></a>
{% endif %}
"""


class _DecisionActionsColumn(columns.ActionsColumn):
    """Marks each row with whether the viewer can decide it, looking up the
    viewer's groups once per table instead of once per row."""
    def render(self, record, table, **kwargs):
        group_ids = getattr(table, '_nbca_group_ids', None)
        request = getattr(table, 'context', {}).get('request') if hasattr(table, 'context') else None
        user = getattr(request, 'user', None)
        if group_ids is None:
            from .absence import decide_group_ids
            group_ids = decide_group_ids(user)
            table._nbca_group_ids = group_ids
        record.nbca_can_decide = bool(user is not None and (
            user.is_superuser or (record.approver_group_id and record.approver_group_id in group_ids)
        ))
        return super().render(record, table, **kwargs)


class ChangeRequestTable(NetBoxTable):
    actions = _DecisionActionsColumn(actions=('changelog',), extra_buttons=_DECISION_BUTTONS)
    id = tables.Column(linkify=True, verbose_name=_('Request'))
    content_type = columns.ContentTypeColumn(verbose_name=_('Object type'))
    object_repr = tables.Column(verbose_name=_('Object'))
    action = columns.ChoiceFieldColumn()
    status = columns.ChoiceFieldColumn()
    auto_approved = columns.BooleanColumn()
    submitted_by = tables.Column(linkify=True)
    approver_group = tables.Column(linkify=True)
    decided_by = tables.Column(linkify=True, empty_values=())
    batch_id = tables.Column(verbose_name=_('Batch'))

    class Meta(NetBoxTable.Meta):
        model = ChangeRequest
        fields = (
            'pk', 'id', 'content_type', 'object_repr', 'action', 'status', 'auto_approved',
            'submitted_by', 'submitted_at', 'approver_group', 'decided_by', 'decided_at',
            'decision_comment', 'applied_at', 'batch_id', 'revision', 'required_approvals', 'expires_at', 'reason',
        )
        default_columns = (
            'id', 'content_type', 'object_repr', 'action', 'status', 'submitted_by',
            'submitted_at', 'approver_group',
        )

    def render_decided_by(self, value, record):
        # Auto-approved: the rule is the approver.
        if value is None and record.approving_rule:
            if record.auto_approved_by_rule_id:
                # By id: loading each row's rule would cost a query per row.
                url = reverse('plugins:netbox_change_approval:approvalrule', args=[record.auto_approved_by_rule_id])
                return format_html('<a href="{}">{}</a>', url, record.approving_rule)
            return record.approving_rule
        if value is None:
            return '—'
        return format_html('<a href="{}">{}</a>', value.get_absolute_url(), value) if hasattr(value, 'get_absolute_url') else str(value)
