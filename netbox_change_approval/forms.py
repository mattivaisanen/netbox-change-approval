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
    ActionChoices, ApprovalRule, AutoApprovalActionChoices, ChangeRequest, RuleOutcomeChoices, StatusChoices,
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
            'Optional filter in the same format as NetBox permission constraints. '
            'Leave blank to match all objects.'
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content_type'].queryset = _gated_object_types()


class ApprovalRuleForm(BaseRuleForm):
    approver_group = DynamicModelChoiceField(
        queryset=Group.objects.all(),
        required=False,
        label=_('Approver group'),
        help_text=_('Only for "Needs approval". Leave blank to use the default approver group.'),
    )

    fieldsets = (
        FieldSet('description', 'priority', 'enabled', name=_('Rule')),
        FieldSet('content_type', 'constraint', 'action', name=_('Applies to')),
        FieldSet('outcome', 'approver_group', 'required_approvals', 'expire_after_days', name=_('Outcome')),
    )

    class Meta:
        model = ApprovalRule
        fields = (
            'priority', 'content_type', 'constraint', 'action', 'outcome', 'approver_group',
            'required_approvals', 'expire_after_days', 'enabled', 'description', 'tags',
        )

    def clean(self):
        super().clean()
        group = self.cleaned_data.get('approver_group')
        needed = self.cleaned_data.get('required_approvals') or 1
        if self.cleaned_data.get('outcome') == RuleOutcomeChoices.REQUIRE and needed > 1:
            if group is None:
                from .gating import _default_approver_group
                group = _default_approver_group()
            members = group.users.filter(is_active=True).count() if group is not None else 0
            # The submitter can't approve their own request, so one more member
            # than approvals needed is required for everyone to be able to submit.
            if members < needed:
                raise forms.ValidationError({
                    'required_approvals': _(
                        'The approver group has only %(members)d active member(s), so %(needed)d '
                        'approvals could never be reached.'
                    ) % {'members': members, 'needed': needed},
                })


class ApprovalRuleFilterForm(NetBoxModelFilterSetForm):
    model = ApprovalRule
    content_type = ContentTypeChoiceField(queryset=ObjectType.objects.none(), required=False)
    outcome = forms.MultipleChoiceField(choices=RuleOutcomeChoices.choices, required=False)
    action = forms.MultipleChoiceField(choices=AutoApprovalActionChoices.choices, required=False)
    enabled = forms.NullBooleanField(required=False, widget=forms.Select(choices=(
        ('', '---------'), ('true', _('Yes')), ('false', _('No')),
    )))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content_type'].queryset = _gated_object_types()


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


class ChangeRequestSendBackForm(ConfirmationForm):
    comment = forms.CharField(
        label=_('What needs to change'),
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_(
            'Required. Shown to the submitter, who can revise and resubmit the same request. '
            'Also applied to every other request in this batch, if any.'
        ),
    )


class ChangeRequestBulkRejectForm(forms.Form):
    comment = forms.CharField(
        label=_('Reason'),
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Required. Shown to every submitter of the selected requests.'),
    )


class ChangeRequestCommentForm(forms.Form):
    text = forms.CharField(
        label=_('Comment'),
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control', 'placeholder': _('Ask or answer something…')}),
    )
