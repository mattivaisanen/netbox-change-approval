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


class WidgetDecidesInPlaceTests(_Base):
    """The widget's dialogs send their form in the background: on success
    the server answers with the message instead of a redirect, so the
    dashboard doesn't reload."""

    INPLACE = {'HTTP_HX_REQUEST': 'true', 'HTTP_X_NBCA_INPLACE': '1'}

    def setUp(self):
        self._rule()
        self.change_request = self._update_request('Inplace')
        self.client.force_login(self.alice)

    def _url(self, name):
        return reverse(f'plugins:netbox_change_approval:changerequest_{name}', kwargs={'pk': self.change_request.pk})

    def test_widget_links_ask_for_in_place(self):
        request = RequestFactory().get('/')
        request.user = self.alice
        html = ApprovalsWidget().render(request)
        self.assertEqual(html.count('data-nbca-modal data-nbca-inplace'), 3)

    def test_approve_answers_with_message(self):
        response = self.client.post(self._url('approve'), {'confirm': True, 'return_url': '/'}, **self.INPLACE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('X-NBCA-Done'), '1')
        self.assertContains(response, 'django-messages')
        self.assertContains(response, 'Approved')
        self.assertEqual(ChangeRequest.objects.get(pk=self.change_request.pk).status, StatusChoices.APPLIED)

    def test_reject_answers_with_message(self):
        response = self.client.post(
            self._url('reject'), {'confirm': True, 'comment': 'No', 'return_url': '/'}, **self.INPLACE,
        )
        self.assertEqual(response.headers.get('X-NBCA-Done'), '1')
        self.assertContains(response, 'Rejected')

    def test_missing_comment_shows_dialog_again(self):
        response = self.client.post(self._url('sendback'), {'confirm': True, 'return_url': '/'}, **self.INPLACE)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get('X-NBCA-Done'))
        self.assertContains(response, 'modal-header')

    def test_failure_still_moves_to_request(self):
        ChangeRequest.objects.filter(pk=self.change_request.pk).update(status=StatusChoices.REJECTED)
        response = self.client.post(self._url('approve'), {'confirm': True, 'return_url': '/'}, **self.INPLACE)
        self.assertIsNone(response.headers.get('X-NBCA-Done'))
        self.assertEqual(response.headers.get('HX-Redirect'), self.change_request.get_absolute_url())

    def test_without_in_place_header_redirect_unchanged(self):
        response = self.client.post(
            self._url('approve'), {'confirm': True, 'return_url': '/'}, HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.headers.get('HX-Redirect'), '/')
