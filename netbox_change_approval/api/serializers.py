"""
Two sets of read-only serializers:

- ChangeRequestSerializer / ApprovalRuleSerializer: the ones NetBox looks up
  by name (utilities.api.get_serializer_for_model) when it needs a change
  request or rule as data: Event Rules, webhooks, scripts, and the changed
  object in the change log (/api/core/object-changes/). Their fields are what
  webhook templates use, so keep them stable.
- *APISerializer: the plugin's REST API (api/views.py) under
  /api/plugins/change-approval/, shaped like NetBox's own API (API `url`,
  `display_url`, `?fields=`/`?omit=`/`?brief=`) and without the costly
  webhook-only fields (recipients, message, ...).
"""
from rest_framework import serializers

from netbox.api.fields import ChoiceField
from netbox.api.serializers import BaseModelSerializer

from ..models import ActionChoices, ApprovalRule, ChangeRequest, ChangeRequestApproval, StatusChoices


def _user(user):
    if user is None:
        return None
    if not hasattr(user, 'pk'):  # DeletedUser: only the kept name
        return {'id': None, 'username': user.get_username(), 'email': ''}
    return {'id': user.pk, 'username': user.username, 'email': user.email or ''}


class _NestableMixin:
    """Accepts the keyword arguments NetBox passes to any serializer it finds
    by name: `nested=True` when the object is shown inside another one (e.g.
    `changed_object` of a change log entry), `fields`/`omit` to pick fields.
    Nested, only Meta.brief_fields are shown."""

    def __init__(self, *args, nested=False, fields=None, omit=None, **kwargs):
        self.nested = nested
        if nested and not fields and not omit:
            fields = self.Meta.brief_fields
        self._include_fields = fields or []
        self._omit_fields = omit or []
        super().__init__(*args, **kwargs)

    def get_fields(self):
        fields = super().get_fields()
        if self._include_fields:
            fields = {name: f for name, f in fields.items() if name in self._include_fields}
        return {name: f for name, f in fields.items() if name not in self._omit_fields}


class ChangeRequestSerializer(_NestableMixin, serializers.ModelSerializer):
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
    last_comment = serializers.SerializerMethodField()
    message = serializers.SerializerMethodField()
    revision_changes = serializers.SerializerMethodField()

    class Meta:
        model = ChangeRequest
        fields = (
            'id', 'display', 'url', 'object_type', 'object_id', 'object_repr', 'action', 'status',
            'auto_approved', 'auto_approved_by_rule', 'diff', 'revision', 'batch_id', 'submitted_by', 'submitted_at', 'approver_group',
            'required_approvals', 'reason', 'decided_by', 'decided_at', 'decision_comment', 'applied_at', 'expires_at',
            'created', 'last_updated', 'link', 'recipients', 'recipient_emails',
            'last_comment', 'message', 'revision_changes',
        )
        brief_fields = ('id', 'display', 'url', 'object_type', 'object_repr', 'action', 'status')
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

    def get_message(self, obj):
        """Ready-to-send text, see notifications.message_text(). A new
        comment sets its own (notifications.fire_object_updated())."""
        if getattr(obj, '_nbca_message', None):
            return obj._nbca_message
        from django.utils.translation import gettext as _

        from ..models import StatusChoices
        from ..notifications import message_text

        if obj.status == StatusChoices.PENDING:
            actor = obj.submitted_by_label
            title = _('Change request updated') if obj.revision > 1 else _('New change request')
            comment = ''
        else:
            actor = obj.decided_by_label
            title = _('Change request {status}').format(status=obj.get_status_display().lower())
            comment = obj.decision_comment
        return message_text(obj, str(title), actor, comment=comment)

    def get_revision_changes(self, obj):
        from ..notifications import revision_changes
        return revision_changes(obj)

    def get_last_comment(self, obj):
        comment = obj.comments.order_by('-created', '-pk').first() if obj.pk else None
        if comment is None:
            return None
        return {'author': _user(comment.author_label), 'text': comment.text, 'created': comment.created.isoformat()}

    def get_object_type(self, obj):
        return f'{obj.content_type.app_label}.{obj.content_type.model}' if obj.content_type_id else None

    def get_submitted_by(self, obj):
        return _user(obj.submitted_by_label)

    def get_decided_by(self, obj):
        return _user(obj.decided_by_label)

    def get_auto_approved_by_rule(self, obj):
        if not obj.approving_rule:
            return None
        return {'id': obj.auto_approved_by_rule_id, 'name': obj.approving_rule}

    def get_approver_group(self, obj):
        return obj.approver_group_label or None


class ApprovalRuleSerializer(_NestableMixin, serializers.ModelSerializer):
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
        brief_fields = ('id', 'display', 'url', 'description')
        read_only_fields = fields

    def get_display(self, obj):
        return str(obj)

    def get_url(self, obj):
        return obj.get_absolute_url()

    def get_object_type(self, obj):
        return f'{obj.content_type.app_label}.{obj.content_type.model}' if obj.content_type_id else None

    def get_approver_group(self, obj):
        return obj.approver_group.name if obj.approver_group_id else None


# --- REST API (api/views.py) ---

def _api_user(user) -> dict | None:
    """{id, username} of a user; id is None once the user is deleted."""
    if user is None:
        return None
    return {'id': getattr(user, 'pk', None), 'username': user.get_username()}


def _username(user) -> str | None:
    """Username of a user, also of a deleted one (its name is kept)."""
    return user.get_username() if user is not None else None


class ChangeRequestAPISerializer(BaseModelSerializer):
    object_type = serializers.SerializerMethodField()
    action = ChoiceField(choices=ActionChoices.choices, read_only=True)
    status = ChoiceField(choices=StatusChoices.choices, read_only=True)
    # Users as plain values rather than nested objects, so dashboards
    # (e.g. Grafana) can use them as columns directly. The id is null once
    # the user is deleted; the username is kept. The view selects the user
    # rows along with the page.
    submitted_by_id = serializers.IntegerField(read_only=True, allow_null=True)
    submitted_by_username = serializers.SerializerMethodField()
    decided_by_id = serializers.IntegerField(read_only=True, allow_null=True)
    decided_by_username = serializers.SerializerMethodField()
    # Method fields named like the foreign keys they read, so NetBox's API
    # prefetches those rows for a whole page at once.
    approver_group = serializers.SerializerMethodField()
    auto_approved_by_rule = serializers.SerializerMethodField()

    class Meta:
        model = ChangeRequest
        fields = (
            'id', 'url', 'display_url', 'display', 'object_type', 'object_id', 'object_repr', 'action', 'status',
            'auto_approved', 'auto_approved_by_rule', 'revision', 'batch_id', 'submitted_by_id',
            'submitted_by_username', 'submitted_at', 'approver_group', 'required_approvals', 'reason',
            'decided_by_id', 'decided_by_username', 'decided_at', 'decision_comment',
            'applied_at', 'expires_at', 'diff', 'created', 'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'object_type', 'object_repr', 'action', 'status')
        read_only_fields = fields

    def get_object_type(self, obj) -> str | None:
        return f'{obj.content_type.app_label}.{obj.content_type.model}' if obj.content_type_id else None

    def get_submitted_by_username(self, obj) -> str | None:
        return _username(obj.submitted_by_label)

    def get_decided_by_username(self, obj) -> str | None:
        return _username(obj.decided_by_label)

    def get_approver_group(self, obj) -> str | None:
        return obj.approver_group_label or None

    def get_auto_approved_by_rule(self, obj) -> str | None:
        return obj.approving_rule or None


class ChangeRequestApprovalAPISerializer(BaseModelSerializer):
    # Approvals have no page of their own: no `url`/`display_url`.
    url = None
    display_url = None
    change_request = ChangeRequestAPISerializer(nested=True, read_only=True)
    user = serializers.SerializerMethodField()

    class Meta:
        model = ChangeRequestApproval
        fields = ('id', 'display', 'change_request', 'user', 'revision', 'created')
        brief_fields = ('id', 'display', 'user', 'revision', 'created')
        read_only_fields = fields

    def get_user(self, obj) -> dict | None:
        return _api_user(obj.user_label)


class ApprovalRuleAPISerializer(BaseModelSerializer):
    object_type = serializers.SerializerMethodField()
    approver_group = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalRule
        fields = (
            'id', 'url', 'display_url', 'display', 'is_default', 'priority', 'object_type', 'constraint', 'action',
            'outcome', 'approver_group', 'required_approvals', 'require_reason', 'expire_after_days', 'enabled',
            'description', 'created', 'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'description')
        read_only_fields = fields

    def get_object_type(self, obj) -> str | None:
        return f'{obj.content_type.app_label}.{obj.content_type.model}' if obj.content_type_id else None

    def get_approver_group(self, obj) -> str | None:
        return obj.approver_group.name if obj.approver_group_id else None
