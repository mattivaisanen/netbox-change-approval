from django.urls import reverse

from .test_approval_features import _Base


class LiveRefreshTests(_Base):
    """The background refresh redraws the sidebar count and, only when
    something changed, the Pending Approvals widget."""

    url = reverse('plugins:netbox_change_approval:live_refresh')

    def setUp(self):
        self._rule()

    def test_nav_only_without_signature(self):
        self.client.force_login(self.alice)
        data = self.client.get(self.url, {'path': '/'}).json()
        self.assertIn('nbca-inbox-nav', data['nav'])
        self.assertNotIn('widget', data)

    def test_count_follows_new_requests(self):
        self.client.force_login(self.alice)
        before = self.client.get(self.url, {'path': '/'}).json()['nav']
        self.assertNotIn('nbca-inbox-count', before)
        self._update_request()
        after = self.client.get(self.url, {'path': '/'}).json()['nav']
        self.assertIn('nbca-inbox-count-urgent', after)

    def test_widget_redrawn_only_when_changed(self):
        self.client.force_login(self.alice)
        first = self.client.get(self.url, {'sig': ''}).json()
        self.assertIn('data-nbca-live="widget"', first['widget'])
        self.assertIn(f'data-nbca-sig="{first["sig"]}"', first['widget'])

        same = self.client.get(self.url, {'sig': first['sig']}).json()
        self.assertEqual(same['sig'], first['sig'])
        self.assertIsNone(same['widget'])

        change_request = self._update_request()
        changed = self.client.get(self.url, {'sig': first['sig']}).json()
        self.assertNotEqual(changed['sig'], first['sig'])
        self.assertIn(f'nbca-wq-{change_request.pk}', changed['widget'])

    def test_signed_out_gets_403(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))

    def test_inbox_page_marks_button_active(self):
        self.client.force_login(self.alice)
        inbox = reverse('plugins:netbox_change_approval:changerequest_inbox')
        nav = self.client.get(self.url, {'path': inbox}).json()['nav']
        self.assertIn('flex-grow-1 active', nav)

    def test_every_page_carries_the_refresh_script(self):
        self.client.force_login(self.alice)
        self.assertContains(self.client.get('/'), 'window.nbcaLive')
