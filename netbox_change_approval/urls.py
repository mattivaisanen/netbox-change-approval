from django.urls import include, path

from utilities.urls import get_model_urls

from . import views  # noqa: F401  (import triggers @register_model_view registration)

app_name = 'netbox_change_approval'

urlpatterns = [
    path('rules/', include(get_model_urls('netbox_change_approval', 'approvalrule', detail=False))),
    path('rules/<int:pk>/', include(get_model_urls('netbox_change_approval', 'approvalrule'))),

    path('exempt-models/', include(get_model_urls('netbox_change_approval', 'exemptmodel', detail=False))),
    path('exempt-models/<int:pk>/', include(get_model_urls('netbox_change_approval', 'exemptmodel'))),

    path('routing-rules/', include(get_model_urls('netbox_change_approval', 'approvalroutingrule', detail=False))),
    path('routing-rules/<int:pk>/', include(get_model_urls('netbox_change_approval', 'approvalroutingrule'))),

    path('auto-approval-rules/', include(get_model_urls('netbox_change_approval', 'autoapprovalrule', detail=False))),
    path('auto-approval-rules/<int:pk>/', include(get_model_urls('netbox_change_approval', 'autoapprovalrule'))),

    path('change-requests/', include(get_model_urls('netbox_change_approval', 'changerequest', detail=False))),
    path('change-requests/<int:pk>/', include(get_model_urls('netbox_change_approval', 'changerequest'))),
]
