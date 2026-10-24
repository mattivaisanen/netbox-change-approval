"""
Serializers NetBox looks up by name (utilities.api.get_serializer_for_model)
when it needs a change request or approval rule as data: Event Rules on
"Object created/updated/deleted", webhooks, and scripts. This plugin has no
REST endpoints of its own, so these are plain read-only serializers without
the API links NetBox's own serializers carry.
"""
from rest_framework import serializers

from ..models import ApprovalRule, ChangeRequest


def _user(user):
    if user is None:
        return None
    return {'id': user.pk, 'username': user.username, 'email': user.email or ''}


class ChangeRequestSerializer(serializers.ModelSerializer):
    display = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    object_type = serializers.SerializerMethodField()
    submitted_by = serializers.SerializerMethodField()
    decided_by = serializers.SerializerMethodField()
    auto_approved_by_rule = serializers.SerializerMethodField()
    approver_group = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField()
    recipients = serializers.SerializerMethodField()
    recipient_emails = serializers.SerializerMethodField()

    class Meta:
        model = ChangeRequest
        fields = (
            'id', 'display', 'url', 'object_type', 'object_id', 'object_repr', 'action', 'status',
            'auto_approved', 'auto_approved_by_rule', 'diff', 'revision', 'batch_id', 'submitted_by', 'submitted_at', 'approver_group',
            'required_approvals', 'reason', 'decided_by', 'decided_at', 'decision_comment', 'applied_at', 'expires_at',
            'created', 'last_updated', 'link', 'recipients', 'recipient_emails',
        )
        read_only_fields = fields

    def get_display(self, obj):
        return str(obj)

    def get_url(self, obj):
        return obj.get_absolute_url()

    def get_link(self, obj):
        from ..notifications import absolute_url
        return absolute_url(obj.get_absolute_url())

    def _recipients(self, obj):
        if not hasattr(obj, '_nbca_recipients'):
            from ..notifications import who_to_notify
            obj._nbca_recipients = who_to_notify(obj)
        return obj._nbca_recipients

    def get_recipients(self, obj):
        return [_user(u) for u in self._recipients(obj)]

    def get_recipient_emails(self, obj):
        return ';'.join(u.email for u in self._recipients(obj) if u.email)

    def get_object_type(self, obj):
        return f'{obj.content_type.app_label}.{obj.content_type.model}' if obj.content_type_id else None

    def get_submitted_by(self, obj):
        return _user(obj.submitted_by)

    def get_decided_by(self, obj):
        return _user(obj.decided_by)

    def get_auto_approved_by_rule(self, obj):
        if not obj.approving_rule:
            return None
        return {'id': obj.auto_approved_by_rule_id, 'name': obj.approving_rule}

    def get_approver_group(self, obj):
        return obj.approver_group.name if obj.approver_group_id else None


class ApprovalRuleSerializer(serializers.ModelSerializer):
    display = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    object_type = serializers.SerializerMethodField()
    approver_group = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalRule
        fields = (
            'id', 'display', 'url', 'is_default', 'priority', 'object_type', 'constraint', 'action', 'outcome',
            'approver_group', 'required_approvals', 'require_reason', 'expire_after_days', 'enabled', 'description',
            'created', 'last_updated',
        )
        read_only_fields = fields

    def get_display(self, obj):
        return str(obj)

    def get_url(self, obj):
        return obj.get_absolute_url()

    def get_object_type(self, obj):
        return f'{obj.content_type.app_label}.{obj.content_type.model}' if obj.content_type_id else None

    def get_approver_group(self, obj):
        return obj.approver_group.name if obj.approver_group_id else None
