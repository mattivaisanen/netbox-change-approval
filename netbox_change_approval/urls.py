from django.urls import include, path

from utilities.urls import get_model_urls

from . import settings_page, views  # noqa: F401  (import triggers @register_model_view registration)

app_name = 'netbox_change_approval'

urlpatterns = [
    path('away/', views.AwayView.as_view(), name='away'),
    path('live/', views.LiveRefreshView.as_view(), name='live_refresh'),
    path('work-sheet/', views.WorkSheetView.as_view(), name='worksheet'),
    path('settings/', settings_page.PluginSettingsView.as_view(), name='settings'),
    path('change-sets/', views.ChangeSetListView.as_view(), name='changeset_list'),
    path('change-sets/<int:pk>/', views.ChangeSetView.as_view(), name='changeset'),
    path('rules/', include(get_model_urls('netbox_change_approval', 'approvalrule', detail=False))),
    path('rules/<int:pk>/', include(get_model_urls('netbox_change_approval', 'approvalrule'))),

    path('change-requests/', include(get_model_urls('netbox_change_approval', 'changerequest', detail=False))),
    path('change-requests/<int:pk>/', include(get_model_urls('netbox_change_approval', 'changerequest'))),
]
