from django.test import RequestFactory
from django.urls import reverse

from ..models import ChangeRequest
from ..widgets import ApprovalsWidget
from .test_approval_features import _Base


class WidgetClosedRequestsTests(_Base):
    """Your rejected and expired requests show on the dashboard widget until
    you mark them read (or open them)."""

    def setUp(self):
        self._rule()

    def _render(self, user):
        request = RequestFactory().get('/')
        request.user = user
        return ApprovalsWidget().render(request)

    def _mark_read_url(self, change_request):
        return reverse('plugins:netbox_change_approval:changerequest_mark_read', kwargs={'pk': change_request.pk})

    def test_rejected_and_expired_show_with_mark_as_read(self):
        rejected = self._update_request('Rejected')
        rejected.reject(self.alice, 'Wrong rack')
        expired = self._update_request('Expired')
        expired.expire()

        html = self._render(self.submitter)
        self.assertIn(f'nbca-wq-{rejected.pk}', html)
        self.assertIn(f'nbca-wq-{expired.pk}', html)
        self.assertIn('Wrong rack', html)
        self.assertIn('Not decided in time', html)
        self.assertIn(self._mark_read_url(rejected), html)
        self.assertNotIn('nbca-idle', html)
        # Only the submitter's widget lists them.
        self.assertNotIn(f'nbca-wq-{rejected.pk}', self._render(self.bob))

    def test_mark_as_read_removes_row_for_good(self):
        rejected = self._update_request()
        rejected.reject(self.alice, '')
        self.client.force_login(self.submitter)

        response = self.client.post(self._mark_read_url(rejected), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 204)
        rejected.refresh_from_db()
        self.assertTrue(rejected.seen_by_submitter)
        html = self._render(self.submitter)
        self.assertNotIn(f'nbca-wq-{rejected.pk}', html)
        self.assertIn('nbca-idle', html)

    def test_mark_as_read_without_javascript_returns_to_dashboard(self):
        rejected = self._update_request()
        rejected.reject(self.alice, '')
        self.client.force_login(self.submitter)
        response = self.client.post(self._mark_read_url(rejected), {'return_url': 'https://example.com/'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_only_the_submitter_can_mark_read(self):
        rejected = self._update_request()
        rejected.reject(self.alice, '')
        self.client.force_login(self.bob)
        self.client.post(self._mark_read_url(rejected))
        self.assertFalse(ChangeRequest.objects.get(pk=rejected.pk).seen_by_submitter)

    def test_mark_as_read_takes_whole_batch(self):
        first, second = self._update_request('One'), self._update_request('Two')
        ChangeRequest.objects.filter(pk__in=(first.pk, second.pk)).update(
            batch_id='00000000-0000-0000-0000-000000000001',
        )
        for change_request in (first, second):
            ChangeRequest.objects.get(pk=change_request.pk).reject(self.alice, '')

        html = self._render(self.submitter)
        self.assertEqual(html.count('nbca-mark-read"'), 1)
        self.client.force_login(self.submitter)
        self.client.post(self._mark_read_url(first))
        self.assertFalse(ChangeRequest.objects.filter(pk__in=(first.pk, second.pk), seen_by_submitter=False).exists())

    def test_inbox_rejected_rows_have_mark_as_read(self):
        rejected = self._update_request()
        rejected.reject(self.alice, '')
        self.client.force_login(self.submitter)
        inbox_url = reverse('plugins:netbox_change_approval:changerequest_inbox')
        self.assertContains(self.client.get(inbox_url), self._mark_read_url(rejected))

        response = self.client.post(self._mark_read_url(rejected), {'return_url': inbox_url})
        self.assertRedirects(response, inbox_url, fetch_redirect_response=False)
        self.assertNotContains(self.client.get(inbox_url), self._mark_read_url(rejected))
