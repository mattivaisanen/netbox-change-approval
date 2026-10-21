from django.test import TestCase
from django.urls import reverse

from dcim.models import Manufacturer
from users.models import Group, User

from .. import gating
from ..models import ActionChoices
from .utils import fake_request


class ReturnUrlTests(TestCase):
    """return_url only ever sends people to a page on this site."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)

    def setUp(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'new'
        self.change_request = gating.gate(obj, ActionChoices.UPDATE, fake_request(self.alice))
        self.client.force_login(self.approver)

    def _url(self, name):
        return reverse(f'plugins:netbox_change_approval:changerequest_{name}', kwargs={'pk': self.change_request.pk})

    def test_offsite_return_url_is_ignored_after_reject(self):
        for bad in ('https://evil.example/', '//evil.example/', '/\\evil.example/'):
            response = self.client.post(self._url('reject'), {'comment': 'no', 'confirm': True, 'return_url': bad})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response['Location'], reverse('plugins:netbox_change_approval:changerequest_list'))
            self.change_request.status = 'pending'
            self.change_request.save()

    def test_local_return_url_is_kept(self):
        response = self.client.post(self._url('approve'), {'confirm': True, 'return_url': '/dcim/manufacturers/'})
        self.assertEqual(response['Location'], '/dcim/manufacturers/')

    def test_javascript_return_url_not_rendered(self):
        response = self.client.get(self._url('approve') + '?return_url=javascript:alert(1)')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'href="javascript:')

    def test_bulk_decision_rejects_backslash_return_url(self):
        response = self.client.post(reverse('plugins:netbox_change_approval:changerequest_bulk_reject'), {
            'pk': [self.change_request.pk], '_confirm': '1', 'comment': 'no', 'return_url': '/\\evil.example/',
        })
        self.assertEqual(response['Location'], reverse('plugins:netbox_change_approval:changerequest_approvals'))
