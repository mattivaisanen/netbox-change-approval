from django.test import TestCase
from django.urls import reverse

from extras.models import Dashboard
from users.models import User


class DashboardLayoutTests(TestCase):
    """Saving the dashboard layout (Unlock, drag, Save) must stick with this
    plugin installed: it's a REST API PATCH to a URL without an object id,
    which the plugin must not treat as a gated bulk write."""

    def test_saved_layout_is_kept(self):
        user = User.objects.create(username='dash-user', is_superuser=True)
        self.client.force_login(user)
        self.client.get(reverse('home'))  # creates the user's default dashboard
        dashboard = Dashboard.objects.get(user=user)
        layout = [dict(item, y=index * 10) for index, item in enumerate(reversed(dashboard.layout))]

        response = self.client.patch(
            reverse('extras-api:dashboard'), {'layout': layout},
            content_type='application/json', HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content[:300])
        dashboard.refresh_from_db()
        self.assertEqual([item['id'] for item in dashboard.layout], [item['id'] for item in layout])
