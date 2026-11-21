import uuid

from django.test import TestCase

from dcim.models import Manufacturer
from netbox.context import current_request
from users.models import User

from .. import public
from ..context import batch_scope, current_batch
from ..exceptions import ChangeApprovalRequired
from ..models import ActionChoices, ApprovalRule, ChangeRequest, StatusChoices
from .utils import fake_request


class PublicInterfaceTests(TestCase):
    """netbox_change_approval.public: the stable surface for other plugins."""

    def setUp(self):
        self.alice = User.objects.create(username='alice')

    def test_constants_match_choices(self):
        for choice in StatusChoices:
            self.assertEqual(getattr(public, f'STATUS_{choice.name}'), choice.value)
        for choice in ActionChoices:
            self.assertEqual(getattr(public, f'ACTION_{choice.name}'), choice.value)
        self.assertIs(public.StatusChoices, StatusChoices)
        self.assertIs(public.ActionChoices, ActionChoices)
        self.assertIs(public.ChangeApprovalRequired, ChangeApprovalRequired)

    def test_all_names_exist(self):
        for name in public.__all__:
            self.assertTrue(hasattr(public, name), name)

    def test_old_internal_names_still_work(self):
        # Other plugins import these directly; they must keep working.
        batch_id = uuid.uuid4()
        with batch_scope(batch_id):
            self.assertEqual(current_batch.get(), batch_id)
            self.assertEqual(public.current_batch(), batch_id)

    def test_batch_groups_requests(self):
        token = current_request.set(fake_request(self.alice))
        self.addCleanup(current_request.reset, token)
        self.assertIsNone(public.current_batch())

        with public.batch() as batch_id:
            self.assertEqual(public.current_batch(), batch_id)
            first = Manufacturer(name='A', slug='a')
            first.full_clean()
            first.save()
            second = Manufacturer(name='B', slug='b')
            second.full_clean()
            second.save()

        self.assertIsNone(public.current_batch())
        self.assertEqual(
            set(ChangeRequest.objects.filter(batch_id=batch_id).values_list('object_id', flat=True)),
            {first.pk, second.pk},
        )

    def test_batch_with_given_id(self):
        batch_id = uuid.uuid4()
        with public.batch(batch_id) as given:
            self.assertEqual(given, batch_id)

    def test_is_gated(self):
        self.assertTrue(public.is_gated(Manufacturer))
        self.assertTrue(public.is_gated(Manufacturer(name='A', slug='a')))
        self.assertFalse(public.is_gated(ApprovalRule))

    def test_pending_create_for(self):
        existing = Manufacturer.objects.create(name='Old', slug='old')
        self.assertIsNone(public.pending_create_for(existing))
        self.assertIsNone(public.pending_create_for(Manufacturer(name='New', slug='new')))

        token = current_request.set(fake_request(self.alice))
        self.addCleanup(current_request.reset, token)
        added = Manufacturer(name='New', slug='new')
        added.full_clean()
        added.save()

        change_request = public.pending_create_for(added)
        self.assertEqual(change_request.action, public.ACTION_CREATE)
        self.assertEqual(change_request.status, public.STATUS_PENDING)
        self.assertEqual(change_request.object_id, added.pk)

    def test_visible_change_requests(self):
        token = current_request.set(fake_request(self.alice))
        self.addCleanup(current_request.reset, token)
        added = Manufacturer(name='New', slug='new')
        added.full_clean()
        added.save()
        bob = User.objects.create(username='bob')

        self.assertEqual(list(public.visible_change_requests(self.alice)), [public.pending_create_for(added)])
        self.assertEqual(list(public.visible_change_requests(bob)), [])
        self.assertEqual(public.visible_change_requests(self.alice, ChangeRequest.objects.none()).count(), 0)
