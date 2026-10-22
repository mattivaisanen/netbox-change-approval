from django.urls import include, path

from utilities.urls import get_model_urls

from . import views  # noqa: F401  (import triggers @register_model_view registration)

app_name = 'netbox_change_approval'

urlpatterns = [
    path('away/', views.AwayView.as_view(), name='away'),
    path('rules/', include(get_model_urls('netbox_change_approval', 'approvalrule', detail=False))),
    path('rules/<int:pk>/', include(get_model_urls('netbox_change_approval', 'approvalrule'))),

    path('change-requests/', include(get_model_urls('netbox_change_approval', 'changerequest', detail=False))),
    path('change-requests/<int:pk>/', include(get_model_urls('netbox_change_approval', 'changerequest'))),
]
