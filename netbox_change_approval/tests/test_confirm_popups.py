from django.test import TestCase
from django.urls import reverse

from dcim.models import Manufacturer
from users.models import Group, User

from .. import gating
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request

POPUP = {'HTTP_HX_REQUEST': 'true'}


def _url(name, cr, query=''):
    return reverse(f'plugins:netbox_change_approval:changerequest_{name}', kwargs={'pk': cr.pk}) + query


class ConfirmPopupTests(TestCase):
    """Confirmations open as pop-up dialogs over the current page; their full
    pages stay for direct links and browsers without JavaScript."""

    @classmethod
    def setUpTestData(cls):
        approvers = Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(approvers)

    def setUp(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'new'
        self.cr = gating.gate(obj, ActionChoices.UPDATE, fake_request(self.alice))

    def test_popup_is_dialog_contents_posting_back_to_same_view(self):
        self.client.force_login(self.approver)
        for name, button in (('approve', 'Approve'), ('reject', 'Reject'), ('sendback', 'Send back')):
            with self.subTest(name):
                url = _url(name, self.cr, '?return_url=/dcim/manufacturers/')
                response = self.client.get(url, **POPUP)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn('class="modal-header"', html)
                self.assertNotIn('<html', html)
                self.assertIn(f'action="{url}"'.replace('&', '&amp;'), html)
                self.assertIn('value="/dcim/manufacturers/"', html)
                self.assertIn(button, html)

    def test_full_page_still_works(self):
        self.client.force_login(self.approver)
        response = self.client.get(_url('approve', self.cr))
        self.assertContains(response, '<html')
        self.assertNotContains(response, 'class="modal-header"')

    def test_reject_comment_is_required_in_the_browser(self):
        self.client.force_login(self.approver)
        response = self.client.get(_url('reject', self.cr), **POPUP)
        self.assertRegex(response.content.decode(), r'<textarea[^>]* required')

    def test_retract_popup_has_both_choices(self):
        self.client.force_login(self.alice)
        response = self.client.get(_url('retract', self.cr), **POPUP)
        self.assertContains(response, 'Retract and edit')
        self.assertContains(response, 'Retract and discard')

    def test_denied_popup_moves_the_page_instead(self):
        # The submitter can't decide their own request: the whole page goes
        # where the full page would have redirected, with the message there.
        self.client.force_login(self.alice)
        response = self.client.get(_url('approve', self.cr), **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['HX-Redirect'], self.cr.get_absolute_url())

    def test_bulk_popup(self):
        self.client.force_login(self.approver)
        url = reverse('plugins:netbox_change_approval:changerequest_bulk_reject')
        response = self.client.post(url, {'pk': [self.cr.pk]}, **POPUP)
        self.assertContains(response, 'Reject 1 request?')
        self.assertContains(response, 'class="modal-header"')
        self.assertEqual(ChangeRequest.objects.get(pk=self.cr.pk).status, StatusChoices.PENDING)

        empty = self.client.post(url, {}, **POPUP)
        self.assertIn('HX-Redirect', empty)

    def test_links_and_buttons_open_the_popup(self):
        self.client.force_login(self.approver)
        response = self.client.get(self.cr.get_absolute_url())
        self.assertContains(response, 'id="nbca-modal-template"')
        self.assertContains(response, f'data-nbca-modal href="{_url("approve", self.cr)}"')

    def test_template_comments_stay_hidden(self):
        # Django's {# #} only works on one line; a longer one shows as text
        # at the top of every page.
        self.client.force_login(self.approver)
        for url in (self.cr.get_absolute_url(), reverse('plugins:netbox_change_approval:changerequest_approvals')):
            with self.subTest(url):
                response = self.client.get(url)
                self.assertNotContains(response, '{#')
                self.assertNotContains(response, 'Optional: data-nbca-confirm-label')
