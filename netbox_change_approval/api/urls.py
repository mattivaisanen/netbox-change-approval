# NetBox serves this module's urlpatterns under /api/plugins/<base_url>/,
# i.e. /api/plugins/change-approval/.
from netbox.api.routers import NetBoxRouter

from . import views

router = NetBoxRouter()
router.register('change-requests', views.ChangeRequestViewSet)
router.register('approvals', views.ChangeRequestApprovalViewSet)
router.register('rules', views.ApprovalRuleViewSet)

urlpatterns = router.urls
