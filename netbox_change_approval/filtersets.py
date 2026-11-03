import django_filters
from django.utils.translation import gettext as _

from netbox.filtersets import BaseFilterSet, NetBoxModelFilterSet
from utilities.filters import ContentTypeFilter

from .models import ApprovalRule, ChangeRequest, ChangeRequestApproval


class ApprovalRuleFilterSet(NetBoxModelFilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))
    content_type = ContentTypeFilter()
    outcome = django_filters.MultipleChoiceFilter(choices=ApprovalRule._meta.get_field('outcome').choices)
    action = django_filters.MultipleChoiceFilter(choices=ApprovalRule._meta.get_field('action').choices)

    class Meta:
        model = ApprovalRule
        fields = ('id', 'priority', 'content_type', 'approver_group', 'enabled')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(description__icontains=value)


class ChangeRequestFilterSet(NetBoxModelFilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))
    content_type = ContentTypeFilter()
    status = django_filters.MultipleChoiceFilter(choices=ChangeRequest._meta.get_field('status').choices)
    action = django_filters.MultipleChoiceFilter(choices=ChangeRequest._meta.get_field('action').choices)

    class Meta:
        model = ChangeRequest
        # NetBox adds lookups for each: submitted_at__gte=2026-01-01,
        # submitted_by_name__ic=ali, revision__gt=1, ... (handy for dashboards).
        fields = (
            'id', 'content_type', 'action', 'status', 'auto_approved', 'submitted_by',
            'approver_group', 'decided_by', 'batch_id', 'revision', 'required_approvals',
            'submitted_by_name', 'decided_by_name', 'approver_group_name',
            'submitted_at', 'decided_at', 'applied_at', 'expires_at',
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(object_repr__icontains=value)


class ChangeRequestApprovalFilterSet(BaseFilterSet):
    """For the REST API's approvals list (api/views.py)."""

    class Meta:
        model = ChangeRequestApproval
        fields = ('id', 'change_request', 'user', 'user_name', 'revision', 'created')
