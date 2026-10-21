from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import ObjectType
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from users.models import Group
from utilities.forms.fields import ContentTypeChoiceField, DynamicModelChoiceField, JSONField
from utilities.forms.forms import ConfirmationForm
from utilities.forms.rendering import FieldSet

from .discovery import iter_gated_models
from .models import (
    ActionChoices, ApprovalRoutingRule, AutoApprovalRule, ChangeRequest, ExemptModel, StatusChoices,
)


def _gated_object_types():
    query = Q()
    for model in iter_gated_models():
        query |= Q(app_label=model._meta.app_label, model=model._meta.model_name)
    if not query:
        return ObjectType.objects.none()
    return ObjectType.objects.filter(query).order_by('app_label', 'model')


class BaseRuleForm(NetBoxModelForm):
    content_type = ContentTypeChoiceField(
        queryset=ObjectType.objects.none(),
        required=False,
        label=_('Object type'),
        help_text=_('Leave blank to match any gated object type.'),
    )
    constraint = JSONField(
        required=False,
        label=_('Constraint'),
        help_text=_(
            'Django ORM-style filter dict, or a list of such dicts (OR\'d together). '
            'Same format as NetBox object permission constraints, e.g. '
            '{"tenant__slug": "acme"}. Leave blank to match unconditionally.'
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content_type'].queryset = _gated_object_types()


class ApprovalRoutingRuleForm(BaseRuleForm):
    approver_group = DynamicModelChoiceField(
        queryset=Group.objects.all(),
    )

    fieldsets = (
        FieldSet('description', 'priority', 'enabled', name=_('Rule')),
        FieldSet('content_type', 'constraint', name=_('Match')),
        FieldSet('approver_group', name=_('Routes to')),
    )

    class Meta:
        model = ApprovalRoutingRule
        fields = (
            'priority', 'content_type', 'constraint', 'approver_group', 'enabled', 'description', 'tags',
        )


class AutoApprovalRuleForm(BaseRuleForm):
    fieldsets = (
        FieldSet('description', 'priority', 'enabled', name=_('Rule')),
        FieldSet('content_type', 'constraint', 'action', name=_('Match')),
    )

    class Meta:
        model = AutoApprovalRule
        fields = (
            'priority', 'content_type', 'constraint', 'action', 'enabled', 'description', 'tags',
        )


class ExemptModelForm(NetBoxModelForm):
    content_type = ContentTypeChoiceField(
        queryset=ObjectType.objects.none(),
        label=_('Object type'),
        help_text=_('Changes to this object type will not require approval while this exemption is enabled.'),
    )

    fieldsets = (
        FieldSet('content_type', 'enabled', 'description', name=_('Exemption')),
    )

    class Meta:
        model = ExemptModel
        fields = ('content_type', 'enabled', 'description', 'tags')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content_type'].queryset = _gated_object_types()


class ExemptModelFilterForm(NetBoxModelFilterSetForm):
    model = ExemptModel
    content_type = ContentTypeChoiceField(queryset=ObjectType.objects.none(), required=False)
    enabled = forms.NullBooleanField(required=False, widget=forms.Select(choices=(
        ('', '---------'), ('true', _('Yes')), ('false', _('No')),
    )))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content_type'].queryset = _gated_object_types()


class ApprovalRoutingRuleFilterForm(NetBoxModelFilterSetForm):
    model = ApprovalRoutingRule
    content_type = ContentTypeChoiceField(queryset=ObjectType.objects.none(), required=False)
    enabled = forms.NullBooleanField(required=False, widget=forms.Select(choices=(
        ('', '---------'), ('true', _('Yes')), ('false', _('No')),
    )))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content_type'].queryset = _gated_object_types()


class AutoApprovalRuleFilterForm(ApprovalRoutingRuleFilterForm):
    model = AutoApprovalRule
    action = forms.ChoiceField(required=False, choices=ActionChoices.choices)


class ChangeRequestFilterForm(NetBoxModelFilterSetForm):
    model = ChangeRequest
    status = forms.ChoiceField(required=False, choices=StatusChoices.choices)
    action = forms.ChoiceField(required=False, choices=ActionChoices.choices)
    content_type = ContentTypeChoiceField(queryset=ObjectType.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content_type'].queryset = _gated_object_types()


class ChangeRequestRejectForm(ConfirmationForm):
    comment = forms.CharField(
        label=_('Reason'),
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Required. Shown to the submitter; also applied to every other request in this batch, if any.'),
    )
