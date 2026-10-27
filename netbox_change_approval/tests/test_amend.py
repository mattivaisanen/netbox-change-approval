from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import Group, ObjectPermission, User

from .. import gating
from ..models import ActionChoices, ChangeRequest, ChangeRequestApproval, StatusChoices
from .utils import fake_request


class AmendOwnRequestTests(TestCase):
    """The submitter can update their own pending request by editing the
    object; everyone else is still blocked."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.alice = User.objects.create(username='alice')
        cls.bob = User.objects.create(username='bob')
        perm = ObjectPermission.objects.create(name='manufacturers', actions=['view', 'add', 'change', 'delete'])
        perm.object_types.set([ObjectType.objects.get_for_model(Manufacturer)])
        perm.users.set([cls.alice, cls.bob])

    def setUp(self):
        self.vendor = Manufacturer.objects.create(name='Acme', slug='acme', description='original')

    def _edit_url(self):
        return reverse('dcim:manufacturer_edit', kwargs={'pk': self.vendor.pk})

    def _post_edit(self, user, description, amend_pk=None):
        self.client.force_login(user)
        url = self._edit_url() + (f'?nbca_amend={amend_pk}' if amend_pk else '')
        return self.client.post(url, {
            'name': 'Acme', 'slug': 'acme', 'description': description, '_update': '',
        })

    def _pending_update(self, description='first try'):
        response = self._post_edit(self.alice, description)
        self.assertEqual(response.status_code, 302)
        return ChangeRequest.objects.get(object_id=self.vendor.pk, status=StatusChoices.PENDING)

    def test_submitter_update_replaces_request_and_bumps_revision(self):
        change_request = self._pending_update()
        ChangeRequestApproval.objects.create(
            change_request=change_request, user=self.approver, revision=change_request.revision,
        )

        self._post_edit(self.alice, 'second try', amend_pk=change_request.pk)

        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(change_request.revision, 2)
        self.assertEqual(change_request.diff['description'], {'old': 'original', 'new': 'second try'})
        from .. import notifications
        self.assertEqual(notifications.revision_changes(change_request),
                         [{'field': 'description', 'old': 'first try', 'new': 'second try'}])
        self.assertEqual(change_request.current_approvals().count(), 0)
        self.assertEqual(ChangeRequest.objects.filter(object_id=self.vendor.pk).count(), 1)
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.description, 'original')

    def test_other_user_still_blocked(self):
        change_request = self._pending_update()
        self._post_edit(self.bob, 'bob was here', amend_pk=change_request.pk)
        change_request.refresh_from_db()
        self.assertEqual(change_request.revision, 1)
        self.assertEqual(change_request.diff['description']['new'], 'first try')

    def test_other_save_by_submitter_does_not_replace_request(self):
        # A save that doesn't come from the "Update request" page (no amend
        # parameter) must not overwrite what the pending edit asks for.
        change_request = self._pending_update()
        self._post_edit(self.alice, 'from somewhere else')
        change_request.refresh_from_db()
        self.assertEqual(change_request.revision, 1)
        self.assertEqual(change_request.diff['description']['new'], 'first try')
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.description, 'original')

    def test_reverting_everything_withdraws_request(self):
        change_request = self._pending_update()
        self._post_edit(self.alice, 'original', amend_pk=change_request.pk)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.RETRACTED)

    def test_edit_page_opens_with_requested_values_and_update_button(self):
        self._pending_update()
        self.client.force_login(self.alice)
        response = self.client.get(self._edit_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('nbca_amend=', response.url)
        self.assertIn('description=first+try', response.url)

        page = self.client.get(response.url)
        self.assertContains(page, 'Update request')
        self.assertContains(page, 'first try')

    def test_other_user_edit_page_unchanged(self):
        self._pending_update()
        self.client.force_login(self.bob)
        response = self.client.get(self._edit_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Update request')

    def test_pending_create_can_be_updated(self):
        from netbox.context import current_request

        new = Manufacturer(name='Newco', slug='newco')
        request = fake_request(self.alice)
        token = current_request.set(request)
        try:
            gating.gate(new, ActionChoices.CREATE, request)
            new.save()
        finally:
            current_request.reset(token)
        change_request = ChangeRequest.objects.get(object_id=new.pk, action=ActionChoices.CREATE)

        self.client.force_login(self.alice)
        self.client.post(reverse('dcim:manufacturer_edit', kwargs={'pk': new.pk}), {
            'name': 'Newco', 'slug': 'newco', 'description': 'added later', '_update': '',
        })

        new.refresh_from_db()
        self.assertEqual(new.description, 'added later')
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(change_request.revision, 2)
        self.assertEqual(change_request.diff.get('description'), 'added later')

    def test_request_page_offers_update_to_submitter_only(self):
        change_request = self._pending_update()
        self.client.force_login(self.alice)
        self.assertContains(self.client.get(change_request.get_absolute_url()), 'Update request')
        self.client.force_login(self.approver)
        self.assertNotContains(self.client.get(change_request.get_absolute_url()), 'Update request')
