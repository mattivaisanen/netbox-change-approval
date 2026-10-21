import django_filters
from django.utils.translation import gettext as _

from netbox.filtersets import NetBoxModelFilterSet
from utilities.filters import ContentTypeFilter

from .models import ApprovalRoutingRule, AutoApprovalRule, ChangeRequest, ExemptModel


class ExemptModelFilterSet(NetBoxModelFilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))
    content_type = ContentTypeFilter()

    class Meta:
        model = ExemptModel
        fields = ('id', 'content_type', 'enabled')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(description__icontains=value)


class ApprovalRoutingRuleFilterSet(NetBoxModelFilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))
    content_type = ContentTypeFilter()

    class Meta:
        model = ApprovalRoutingRule
        fields = ('id', 'priority', 'content_type', 'approver_group', 'enabled')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(description__icontains=value)


class AutoApprovalRuleFilterSet(NetBoxModelFilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))
    content_type = ContentTypeFilter()

    class Meta:
        model = AutoApprovalRule
        fields = ('id', 'priority', 'content_type', 'action', 'enabled')

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
        fields = (
            'id', 'content_type', 'action', 'status', 'auto_approved', 'submitted_by',
            'approver_group', 'decided_by', 'batch_id',
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(object_repr__icontains=value)
