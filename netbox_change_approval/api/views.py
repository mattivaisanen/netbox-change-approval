"""
Read-only REST API under /api/plugins/change-approval/ (see docs/api.md).
Requests and rules are written through the web UI only, so nothing here
accepts POST/PATCH/DELETE.
"""
from rest_framework.viewsets import ReadOnlyModelViewSet

from netbox.api.authentication import TokenPermissions
from netbox.api.viewsets import NetBoxReadOnlyModelViewSet

from .. import filtersets
from ..models import ApprovalRule, ChangeRequest, ChangeRequestApproval
from . import serializers


class ChangeRequestViewSet(NetBoxReadOnlyModelViewSet):
    queryset = ChangeRequest.objects.select_related('content_type')
    serializer_class = serializers.ChangeRequestAPISerializer
    filterset_class = filtersets.ChangeRequestFilterSet


class ApprovalRuleViewSet(NetBoxReadOnlyModelViewSet):
    queryset = ApprovalRule.objects.select_related('content_type', 'approver_group')
    serializer_class = serializers.ApprovalRuleAPISerializer
    filterset_class = filtersets.ApprovalRuleFilterSet


class ViewChangeRequestsPermission(TokenPermissions):
    """An approval is part of its change request: whoever may view change
    requests may view their approvals. There is no separate permission."""

    def get_required_permissions(self, method, model_cls):
        return super().get_required_permissions(method, ChangeRequest)

    def has_object_permission(self, request, view, obj):
        # get_queryset() already holds only approvals of visible requests.
        return True


class ChangeRequestApprovalViewSet(ReadOnlyModelViewSet):
    queryset = ChangeRequestApproval.objects.all()
    serializer_class = serializers.ChangeRequestApprovalAPISerializer
    filterset_class = filtersets.ChangeRequestApprovalFilterSet
    permission_classes = [ViewChangeRequestsPermission]

    def get_queryset(self):
        # Only approvals of change requests this user may view (object
        # permissions with constraints apply too).
        visible = ChangeRequest.objects.restrict(self.request.user, 'view')
        return super().get_queryset().filter(change_request__in=visible).select_related(
            'user', 'change_request__content_type',
        )
