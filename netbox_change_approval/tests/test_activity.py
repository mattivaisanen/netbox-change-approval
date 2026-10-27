"""
One activity (changelog) entry per user action: a change request only logs
itself when the real object has no entry of its own in the same request, and
otherwise labels that entry's message. See ChangeRequest.to_objectchange().
"""
from django.test import TestCase

from core.models import ObjectChange, ObjectType
from dcim.models import Manufacturer
from netbox.context import current_request
from users.models import Group, User

from .. import gating
from ..models import ActionChoices, ChangeRequest
from .utils import fake_request


class ActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.cr_type = ObjectType.objects.get_for_model(ChangeRequest)
        cls.mfr_type = ObjectType.objects.get_for_model(Manufacturer)

    def _as(self, user):
        request = fake_request(user)
        token = current_request.set(request)
        self.addCleanup(current_request.reset, token)
        return request

    def _entries(self, request):
        return ObjectChange.objects.filter(request_id=request.id)

    def test_pending_create_logs_only_the_object_with_a_label(self):
        request = self._as(self.alice)
        obj = Manufacturer(name='Acme', slug='acme')
        gating.gate(obj, ActionChoices.CREATE, request)
        obj.save()
        cr = ChangeRequest.objects.get()

        entries = list(self._entries(request))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].changed_object_type_id, self.mfr_type.pk)
        self.assertEqual(entries[0].message, f'Waiting for approval (change request #{cr.pk})')

    def test_pending_update_logs_the_request(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        request = self._as(self.alice)
        obj.description = 'new'
        cr = gating.gate(obj, ActionChoices.UPDATE, request)

        entries = list(self._entries(request))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].changed_object_type_id, self.cr_type.pk)
        self.assertEqual(entries[0].message, f'Waiting for approval (change request #{cr.pk})')

    def test_approved_update_logs_only_the_object(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        self._as(self.alice)
        obj.description = 'new'
        cr = gating.gate(obj, ActionChoices.UPDATE, current_request.get())

        request = self._as(self.approver)
        cr.apply(self.approver)

        entries = list(self._entries(request))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].changed_object_type_id, self.mfr_type.pk)
        self.assertEqual(entries[0].message, f'Approved by approver (change request #{cr.pk})')
        # The change is the submitter's; the message names the approver.
        self.assertEqual(entries[0].user, self.alice)
        self.assertEqual(entries[0].user_name, self.alice.username)

    def test_approved_create_logs_the_request(self):
        self._as(self.alice)
        obj = Manufacturer(name='Acme', slug='acme')
        gating.gate(obj, ActionChoices.CREATE, current_request.get())
        obj.save()
        cr = ChangeRequest.objects.get()

        request = self._as(self.approver)
        cr.apply(self.approver)

        entries = list(self._entries(request))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].changed_object_type_id, self.cr_type.pk)
        self.assertEqual(entries[0].message, f'Approved by approver (change request #{cr.pk})')
        self.assertEqual(entries[0].user, self.alice)

    def test_rejected_create_logs_only_the_delete(self):
        self._as(self.alice)
        obj = Manufacturer(name='Acme', slug='acme')
        gating.gate(obj, ActionChoices.CREATE, current_request.get())
        obj.save()
        cr = ChangeRequest.objects.get()

        request = self._as(self.approver)
        cr.reject(self.approver, 'duplicate')

        entries = list(self._entries(request))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].changed_object_type_id, self.mfr_type.pk)
        self.assertEqual(entries[0].message, f'Rejected by approver (change request #{cr.pk}): duplicate')
        self.assertEqual(entries[0].user, self.alice)

    def test_typed_message_is_kept(self):
        request = self._as(self.alice)
        obj = Manufacturer(name='Acme', slug='acme')
        obj._changelog_message = 'ticket 42'
        gating.gate(obj, ActionChoices.CREATE, request)
        obj.save()
        cr = ChangeRequest.objects.get()

        entry = self._entries(request).get()
        self.assertEqual(entry.message, f'ticket 42 | Waiting for approval (change request #{cr.pk})')



class ChangelogTabTests(TestCase):
    """A request's Changelog tab lists the object's entries the request
    labelled, with its own, newest first; nothing else of the object."""
    setUpTestData = classmethod(ActivityTests.setUpTestData.__func__)
    _as = ActivityTests._as

    def _entry(self, obj, message):
        import uuid
        return ObjectChange.objects.create(
            changed_object_type=self.mfr_type, changed_object_id=obj.pk, object_repr=str(obj),
            action='update', request_id=uuid.uuid4(), user_name='alice', message=message,
        )

    def test_changelog_tab_lists_the_requests_history(self):
        from django.urls import reverse

        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        self._as(self.alice)
        obj.description = 'new'
        cr = gating.gate(obj, ActionChoices.UPDATE, current_request.get())  # logs the request itself
        approved = self._as(self.approver)
        cr.apply(self.approver)  # labels the object's own entry instead
        object_entry = ObjectChange.objects.get(request_id=approved.id)
        self.assertEqual(list(cr.log_entries.values_list('object_change_id', flat=True)), [object_entry.pk])

        # Older requests kept no links: their label in the message still finds them.
        older = self._entry(obj, f'ticket 7 | Sent back for revision by approver (change request #{cr.pk})')
        self._entry(obj, 'unrelated edit')
        self._entry(obj, f'Approved (change request #{cr.pk + 1})')

        admin = User.objects.create(username='admin', is_superuser=True)
        self.client.force_login(admin)
        response = self.client.get(
            reverse('plugins:netbox_change_approval:changerequest_changelog', kwargs={'pk': cr.pk}),
        )
        self.assertEqual(response.status_code, 200)
        shown = [row.record for row in response.context['table'].rows]
        own = ObjectChange.objects.get(changed_object_type=self.cr_type, changed_object_id=cr.pk)
        self.assertEqual(shown, [older, object_entry, own])
