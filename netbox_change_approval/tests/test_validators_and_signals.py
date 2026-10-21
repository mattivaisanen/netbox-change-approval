from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase

from dcim.models import Manufacturer
from netbox.context import current_request
from users.models import User

from .. import signals
from ..exceptions import ChangeApprovalRequired
from ..models import ChangeRequest, StatusChoices
from ..validators import ApprovalGateValidator
from .utils import fake_request, grant_bypass


class ApprovalGateValidatorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username='alice')

    def test_create_passes_validation_then_gets_tagged_pending_on_save(self):
        # CREATE doesn't raise/abort (unlike UPDATE) — see gating.gate()'s
        # docstring: the point is for the object to be written immediately so a
        # second person can see it instead of submitting a duplicate. validate()
        # just stashes a marker; signals.finish_pending_create does the rest
        # once save() gives it a real pk.
        unsaved = Manufacturer(name='Acme', slug='acme')

        ApprovalGateValidator().validate(unsaved, fake_request(self.alice))  # should not raise
        unsaved.save()

        self.assertTrue(Manufacturer.objects.filter(slug='acme').exists())
        self.assertTrue(ChangeRequest.objects.filter(object_id=unsaved.pk, status='pending').exists())
        change_request = ChangeRequest.objects.get(object_id=unsaved.pk)
        self.assertEqual(change_request.status, StatusChoices.PENDING)

    def test_bypass_permission_lets_validate_pass_silently(self):
        grant_bypass(self.alice, Manufacturer)
        unsaved = Manufacturer(name='Acme', slug='acme')

        ApprovalGateValidator().validate(unsaved, fake_request(self.alice))  # should not raise

    def test_update_still_raises_and_does_not_write(self):
        # Unchanged by the CREATE rework above: UPDATE still aborts outright —
        # there's no "duplicate edit" problem to solve the way there is for
        # CREATE, so the object stays untouched until a human decides.
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'a pending edit'

        # Single-object edit: escapes the form as ChangeApprovalRequired so the
        # middleware can redirect with one message instead of a form error.
        with self.assertRaises(ChangeApprovalRequired) as ctx:
            ApprovalGateValidator().validate(obj, fake_request(self.alice))

        self.assertEqual(ctx.exception.change_request.status, StatusChoices.PENDING)
        self.assertIsNotNone(ctx.exception.change_request.pk)
        obj.refresh_from_db()
        self.assertEqual(obj.description, '')

    def test_update_in_bulk_edit_still_raises_validation_error(self):
        import uuid

        from ..context import batch_scope

        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'a pending bulk edit'

        with batch_scope(uuid.uuid4()), self.assertRaises(ValidationError):
            ApprovalGateValidator().validate(obj, fake_request(self.alice))

    def test_auto_approved_update_passes_validation(self):
        from core.models import ObjectType

        from ..models import AutoApprovalRule

        AutoApprovalRule.objects.create(
            content_type=ObjectType.objects.get_for_model(Manufacturer), action='update',
        )
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'auto edit'

        ApprovalGateValidator().validate(obj, fake_request(self.alice))  # should not raise
        obj.save()

        obj.refresh_from_db()
        self.assertEqual(obj.description, 'auto edit')
        change_request = ChangeRequest.objects.get(object_id=obj.pk)
        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertTrue(change_request.auto_approved)


class GateDeleteSignalTests(TransactionTestCase):
    """
    TransactionTestCase, not TestCase: NetBox's delete collector
    (netbox/models/deletion.py) wraps obj.delete() in its own
    transaction.atomic(savepoint=False) specifically so that a raised exception
    rolls back the *whole* enclosing transaction, not just the delete — correct
    in a real request (there's no enclosing transaction, so this is harmless), but
    under plain TestCase (which wraps every test in its own outer atomic) that
    same exception poisons the outer test transaction, breaking every assertion
    after it with a TransactionManagementError that has nothing to do with this
    plugin's logic. TransactionTestCase doesn't wrap tests in a transaction, so
    this matches how it actually behaves in production.
    """
    def setUp(self):
        self.alice = User.objects.create(username='alice')

    def _set_current_request(self, request):
        token = current_request.set(request)
        self.addCleanup(current_request.reset, token)

    def test_delete_of_gated_model_is_blocked_and_queued(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        self._set_current_request(fake_request(self.alice))

        # The ChangeRequest is deliberately *unsaved* at this point — see
        # gating.gate()'s docstring. Saving it is ChangeApprovalMiddleware's job,
        # done only after this (NetBox delete-collector) transaction has rolled
        # back — which a unit test calling obj.delete() directly never goes
        # through, so we only assert what the signal itself is responsible for.
        with self.assertRaises(ChangeApprovalRequired) as cm:
            obj.delete()

        change_request = cm.exception.change_request
        self.assertIsNone(change_request.pk)
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(change_request.action, 'delete')
        self.assertEqual(change_request.object_id, obj.pk)
        self.assertFalse(cm.exception.locked)

        self.assertTrue(Manufacturer.objects.filter(pk=obj.pk).exists())
        self.assertFalse(ChangeRequest.objects.filter(object_id=obj.pk).exists())

    def test_locked_delete_reports_the_existing_request(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        self._set_current_request(fake_request(self.alice))
        with self.assertRaises(ChangeApprovalRequired) as cm:
            obj.delete()
        first_request = cm.exception.change_request
        first_request.save()  # what the middleware would have done

        with self.assertRaises(ChangeApprovalRequired) as cm2:
            obj.delete()

        self.assertTrue(cm2.exception.locked)
        self.assertEqual(cm2.exception.change_request.pk, first_request.pk)

    def test_bypass_permission_lets_delete_through(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        grant_bypass(self.alice, Manufacturer)
        self._set_current_request(fake_request(self.alice))

        obj.delete()  # should not raise

        self.assertFalse(Manufacturer.objects.filter(pk=obj.pk).exists())

    def test_gated_models_are_connected_to_pre_delete(self):
        self.assertIn(Manufacturer, list(signals.iter_gated_models()))
