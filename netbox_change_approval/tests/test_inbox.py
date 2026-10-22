import uuid

from django.test import TestCase
from django.urls import reverse

from dcim.models import Manufacturer
from users.models import Group, User

from .. import gating, inbox
from ..context import batch_scope
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request


class InboxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)

    def _update(self, slug, description):
        obj = Manufacturer.objects.get_or_create(name=slug.title(), slug=slug)[0]
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = description
        return gating.gate(obj, ActionChoices.UPDATE, fake_request(self.alice))

    def test_batch_is_one_item(self):
        with batch_scope(uuid.uuid4()):
            for n in range(3):
                self._update(f'vendor-{n}', 'bulk')
        self._update('single', 'one')

        queryset = inbox._decide(self.approver)
        self.assertEqual(queryset.count(), 4)
        self.assertEqual(inbox.item_count(queryset), 2)
        self.assertEqual(inbox.inbox_count(self.approver), 2)
        sizes = sorted(len(item['members']) for item in inbox.group_items(queryset))
        self.assertEqual(sizes, [1, 3])

    def test_pages_keep_batches_whole(self):
        self._update('single-old', 'one')
        with batch_scope(uuid.uuid4()):
            for n in range(3):
                self._update(f'vendor-{n}', 'bulk')
        self._update('single-new', 'two')

        queryset = inbox._decide(self.approver)
        pages = [inbox.group_items(queryset, limit=1, page=n) for n in (1, 2, 3, 4)]
        self.assertEqual([len(page) for page in pages], [1, 1, 1, 0])
        self.assertEqual(sorted(len(page[0]['members']) for page in pages[:3]), [1, 1, 3])
        leads = [page[0]['lead'].pk for page in pages[:3]]
        self.assertEqual(len(set(leads)), 3)

    def test_inbox_page_links(self):
        from unittest import mock

        for n in range(3):
            self._update(f'paged-{n}', 'x')
        self.client.force_login(self.approver)
        with mock.patch.object(inbox, 'SECTION_LIMIT', 2):
            first = self.client.get(reverse('plugins:netbox_change_approval:changerequest_inbox'))
            self.assertContains(first, 'Page 1 of 2')
            self.assertContains(first, 'decide_page=2')
            second = self.client.get(reverse('plugins:netbox_change_approval:changerequest_inbox') + '?decide_page=2')
            self.assertContains(second, 'Page 2 of 2')
            self.assertContains(second, 'decide_page=1')

    def test_page_shows_batch_quick_view_and_approved(self):
        with batch_scope(uuid.uuid4()):
            for n in range(2):
                self._update(f'vendor-{n}', 'bulk text')
        applied = self._update('done', 'approved text')
        applied.apply(user=self.approver)

        self.client.force_login(self.alice)
        response = self.client.get(reverse('plugins:netbox_change_approval:changerequest_inbox'))

        self.assertContains(response, 'Waiting on others')
        self.assertContains(response, 'Update 2 objects')
        self.assertContains(response, 'bulk text')  # quick-view diff, rendered collapsed
        self.assertContains(response, 'Approved')
        self.assertContains(response, 'approved text')
        self.assertContains(response, reverse(
            'plugins:netbox_change_approval:changerequest_changelog', kwargs={'pk': applied.pk},
        ))

    def test_my_requests_redirects_to_inbox(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse('plugins:netbox_change_approval:changerequest_mine'))
        self.assertRedirects(response, reverse('plugins:netbox_change_approval:changerequest_inbox'))

    def test_action_labels_are_requests(self):
        change_request = self._update('acme', 'x')
        self.assertEqual(change_request.get_action_display(), 'Update')
        self.assertEqual(ChangeRequest.objects.get(pk=change_request.pk).status, StatusChoices.PENDING)


class FromDbSignatureTests(TestCase):
    def test_from_db_accepts_newer_django_arguments(self):
        # Django 6.1 passes fetch_mode to Model.from_db(); the override must
        # accept whatever Django adds.
        import inspect

        from ..models import ChangeRequest
        params = inspect.signature(ChangeRequest.from_db).parameters
        self.assertTrue(any(p.kind is p.VAR_KEYWORD for p in params.values()))
