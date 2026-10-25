from django.test import RequestFactory
from django.urls import reverse

from ..models import ChangeRequest, StatusChoices
from ..widgets import ApprovalsWidget
from .test_approval_features import _Base


class WidgetStaysOnDashboardTests(_Base):
    """Approving, sending back, rejecting or discarding from the dashboard
    widget brings the user back to the dashboard, not to the request."""

    def setUp(self):
        self._rule()
        self.change_request = self._update_request('Widget')

    def _url(self, name, change_request=None):
        change_request = change_request or self.change_request
        return reverse(f'plugins:netbox_change_approval:changerequest_{name}', kwargs={'pk': change_request.pk})

    def _render(self, user):
        request = RequestFactory().get('/')
        request.user = user
        return ApprovalsWidget().render(request)

    def test_widget_buttons_return_to_dashboard(self):
        html = self._render(self.alice)
        for name in ('approve', 'sendback', 'reject'):
            self.assertIn(f'{self._url(name)}?return_url=/', html)

    def test_discard_button_returns_to_dashboard(self):
        self.change_request.send_back(self.alice, 'Fix it')
        html = self._render(self.submitter)
        self.assertIn(f'{self._url("discard")}?return_url=/', html)

    def test_approve_lands_on_dashboard(self):
        self.client.force_login(self.alice)
        response = self.client.post(self._url('approve'), {'confirm': True, 'return_url': '/'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_send_back_lands_on_dashboard(self):
        self.client.force_login(self.alice)
        response = self.client.post(self._url('sendback'), {'confirm': True, 'comment': 'Fix', 'return_url': '/'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_reject_lands_on_dashboard(self):
        self.client.force_login(self.alice)
        response = self.client.post(self._url('reject'), {'confirm': True, 'comment': 'No', 'return_url': '/'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_discard_lands_on_dashboard(self):
        self.change_request.send_back(self.alice, 'Fix it')
        self.client.force_login(self.submitter)
        response = self.client.post(self._url('discard'), {'confirm': True, 'return_url': '/'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertEqual(ChangeRequest.objects.get(pk=self.change_request.pk).status, StatusChoices.RETRACTED)

    def test_discard_without_return_url_still_opens_inbox(self):
        self.change_request.send_back(self.alice, 'Fix it')
        self.client.force_login(self.submitter)
        response = self.client.post(self._url('discard'), {'confirm': True})
        self.assertRedirects(
            response, reverse('plugins:netbox_change_approval:changerequest_inbox'), fetch_redirect_response=False,
        )

    def test_foreign_return_url_ignored(self):
        self.client.force_login(self.alice)
        response = self.client.post(self._url('approve'), {'confirm': True, 'return_url': 'https://example.com/'})
        self.assertNotIn('example.com', response.url)
