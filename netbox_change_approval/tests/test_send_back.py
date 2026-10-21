from urllib.parse import parse_qs, urlparse

from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from dcim.models import Manufacturer
from netbox.context import current_request
from users.models import Group, User

from .. import gating, inbox, revision
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request


def _request(user, revise_pk=None):
    request = fake_request(user)
    request.GET = QueryDict(f'{revision.REVISE_PARAM}={revise_pk}' if revise_pk else '')
    return request


class SendBackModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)

    def _submit_update(self, obj, user, description, revise_pk=None):
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = description
        request = _request(user, revise_pk)
        token = current_request.set(request)
        try:
            return gating.gate(obj, ActionChoices.UPDATE, request)
        finally:
            current_request.reset(token)

    def test_update_resubmit_reuses_same_request(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        first = self._submit_update(obj, self.alice, 'first try')
        first.send_back(self.approver, 'Use the full company name')

        first.refresh_from_db()
        self.assertEqual(first.status, StatusChoices.RETURNED)
        self.assertEqual(Manufacturer.objects.get(pk=obj.pk).description, '')

        second = self._submit_update(obj, self.alice, 'second try')

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(ChangeRequest.objects.count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.status, StatusChoices.PENDING)
        self.assertEqual(second.revision, 2)
        self.assertEqual(second.sent_back_comment, 'Use the full company name')
        self.assertEqual(second.sent_back_by, self.approver)
        self.assertIsNone(second.decided_by)
        self.assertEqual(second.diff['description']['new'], 'second try')

    def test_other_users_edit_does_not_take_over_sent_back_request(self):
        bob = User.objects.create(username='bob')
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        first = self._submit_update(obj, self.alice, 'first try')
        first.send_back(self.approver, 'no')

        other = self._submit_update(obj, bob, 'bob edit')

        self.assertNotEqual(other.pk, first.pk)
        first.refresh_from_db()
        self.assertEqual(first.status, StatusChoices.RETURNED)

    def test_create_send_back_removes_object_and_resubmit_reuses_request(self):
        unsaved = Manufacturer(name='Acme', slug='acme')
        request = _request(self.alice)
        token = current_request.set(request)
        try:
            gating.gate(unsaved, ActionChoices.CREATE, request)
            unsaved.save()
        finally:
            current_request.reset(token)
        first = ChangeRequest.objects.get(object_id=unsaved.pk)

        first.send_back(self.approver, 'Wrong slug')
        self.assertFalse(Manufacturer.objects.filter(slug='acme').exists())

        # The Revise button's form posts with ?nbca_revise=<pk>.
        revised = Manufacturer(name='Acme', slug='acme-inc')
        request = _request(self.alice, revise_pk=first.pk)
        token = current_request.set(request)
        try:
            gating.gate(revised, ActionChoices.CREATE, request)
            revised.save()
        finally:
            current_request.reset(token)

        self.assertEqual(ChangeRequest.objects.count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.status, StatusChoices.PENDING)
        self.assertEqual(first.object_id, revised.pk)
        self.assertEqual(first.revision, 2)
        self.assertEqual(first.diff['slug'], 'acme-inc')

    def test_revise_url_prefills_submitted_values(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        change_request = self._submit_update(obj, self.alice, 'new text')
        change_request.send_back(self.approver, 'fix')

        url = urlparse(revision.revise_url(change_request))
        query = parse_qs(url.query)

        self.assertTrue(url.path.endswith(f'/{obj.pk}/edit/'))
        self.assertEqual(query['description'], ['new text'])
        self.assertEqual(query[revision.REVISE_PARAM], [str(change_request.pk)])

    def test_form_initial_booleans_and_custom_fields(self):
        from dcim.models import Interface
        params = revision.form_initial(Interface, {
            'enabled': False, 'mgmt_only': True, 'custom_field_data': {'flag': False, 'n': 3},
        })
        self.assertEqual(params['enabled'], '')  # unticked checkbox
        self.assertEqual(params['mgmt_only'], 'true')
        self.assertEqual(params['cf_flag'], 'false')
        self.assertEqual(params['cf_n'], '3')

    def test_inbox_counts(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        change_request = self._submit_update(obj, self.alice, 'x')
        self.assertEqual(inbox.inbox_count(self.approver), 1)
        self.assertEqual(inbox.inbox_count(self.alice), 0)

        change_request.send_back(self.approver, 'fix')
        self.assertEqual(inbox.inbox_count(self.approver), 0)
        self.assertEqual(inbox.inbox_count(self.alice), 1)

        change_request.retract(self.alice)
        self.assertEqual(inbox.inbox_count(self.alice), 0)

    def test_rejection_counts_until_seen(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        change_request = self._submit_update(obj, self.alice, 'x')
        change_request.reject(self.approver, 'no')
        self.assertEqual(inbox.inbox_count(self.alice), 1)

        inbox.mark_rejections_seen(self.alice)
        self.assertEqual(inbox.inbox_count(self.alice), 0)


class SendBackViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.bob = User.objects.create(username='bob')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)

    def setUp(self):
        self.obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj = Manufacturer.objects.get(pk=self.obj.pk)
        obj.description = 'changed'
        self.change_request = gating.gate(obj, ActionChoices.UPDATE, fake_request(self.alice))

    def _url(self, name):
        return reverse(f'plugins:netbox_change_approval:changerequest_{name}', kwargs={'pk': self.change_request.pk})

    def test_send_back_needs_approver_and_comment(self):
        self.client.force_login(self.bob)
        self.client.post(self._url('sendback'), {'confirm': True, 'comment': 'x'})
        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, StatusChoices.PENDING)

        self.client.force_login(self.approver)
        self.assertEqual(self.client.get(self._url('sendback')).status_code, 200)
        response = self.client.post(self._url('sendback'), {'confirm': True, 'comment': ''})
        self.assertEqual(response.status_code, 200)  # form re-rendered with error
        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, StatusChoices.PENDING)

        self.client.post(self._url('sendback'), {'confirm': True, 'comment': 'Add a description that means something'})
        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, StatusChoices.RETURNED)
        self.assertEqual(self.change_request.decision_comment, 'Add a description that means something')

    def test_submitter_revise_redirects_to_prefilled_edit_form(self):
        self.change_request.send_back(self.approver, 'fix')

        self.client.force_login(self.bob)
        response = self.client.get(self._url('revise'))
        self.assertEqual(response.url, self.change_request.get_absolute_url())

        self.client.force_login(self.alice)
        response = self.client.get(self._url('revise'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/{self.obj.pk}/edit/', response.url)
        self.assertIn(f'{revision.REVISE_PARAM}={self.change_request.pk}', response.url)

        detail = self.client.get(self.change_request.get_absolute_url())
        self.assertContains(detail, 'Revise and resubmit')

    def test_submitter_can_discard(self):
        self.change_request.send_back(self.approver, 'fix')
        self.client.force_login(self.alice)

        self.assertEqual(self.client.get(self._url('discard')).status_code, 200)
        self.client.post(self._url('discard'), {'confirm': True})

        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, StatusChoices.RETRACTED)

    def test_inbox_page_and_navbar_button(self):
        self.client.force_login(self.approver)
        response = self.client.get(reverse('plugins:netbox_change_approval:changerequest_inbox'))
        self.assertContains(response, 'Needs your decision')
        self.assertContains(response, 'nbca-inbox-nav')  # navbar button, rendered on every page

        self.change_request.send_back(self.approver, 'fix')
        self.client.force_login(self.alice)
        response = self.client.get(reverse('plugins:netbox_change_approval:changerequest_inbox'))
        self.assertContains(response, 'Sent back to you')
        self.assertContains(response, 'fix')

    def test_opening_rejected_request_marks_it_seen(self):
        self.change_request.reject(self.approver, 'no')
        self.client.force_login(self.alice)
        self.client.get(self.change_request.get_absolute_url())
        self.change_request.refresh_from_db()
        self.assertTrue(self.change_request.seen_by_submitter)
