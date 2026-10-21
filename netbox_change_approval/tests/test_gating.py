import uuid

from django.test import TestCase, override_settings

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import Group, User

from .. import gating
from ..context import applying_change_on, batch_scope
from ..exceptions import ObjectLocked
from ..models import ActionChoices, ApprovalRoutingRule, AutoApprovalRule, ChangeRequest, StatusChoices
from ..tags import PENDING_TAG_SLUG
from .utils import fake_request, grant_bypass


def _gate_and_save(instance, user):
    """
    Mirrors what actually happens for a create: the validator's gate() call
    (during clean()) stashes a marker and returns None rather than aborting —
    see gating.gate()'s docstring — then the form's own save() proceeds, which
    is where post_save/finish_pending_create (signals.py) picks the marker back
    up, now that a real pk exists.
    """
    result = gating.gate(instance, ActionChoices.CREATE, fake_request(user))
    assert result is None
    instance.save()
    return instance


class GatingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.bob = User.objects.create(username='bob')

    def test_create_is_written_immediately_and_tagged_pending(self):
        # The whole point (see README "Pending creates are visible immediately"):
        # a second person browsing Manufacturers sees this one already, tagged,
        # instead of silently duplicating it.
        unsaved = Manufacturer(name='Acme', slug='acme')
        obj = _gate_and_save(unsaved, self.alice)

        self.assertTrue(Manufacturer.objects.filter(slug='acme').exists())
        self.assertTrue(obj.tags.filter(slug=PENDING_TAG_SLUG).exists())

        change_request = ChangeRequest.objects.get(object_id=obj.pk, action=ActionChoices.CREATE)
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(change_request.submitted_by, self.alice)

    def test_bypass_permission_lets_create_through_untagged(self):
        grant_bypass(self.alice, Manufacturer)
        unsaved = Manufacturer(name='Acme', slug='acme')

        result = gating.gate(unsaved, ActionChoices.CREATE, fake_request(self.alice))

        self.assertIsNone(result)
        self.assertFalse(hasattr(unsaved, '_nbca_pending_create'))
        unsaved.save()
        self.assertFalse(unsaved.tags.filter(slug=PENDING_TAG_SLUG).exists())
        self.assertFalse(ChangeRequest.objects.filter(object_id=unsaved.pk).exists())

    def test_noop_update_is_not_queued(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        fetched = Manufacturer.objects.get(pk=obj.pk)  # no changes made

        result = gating.gate(fetched, ActionChoices.UPDATE, fake_request(self.alice))

        self.assertIsNone(result)

    def test_pending_change_locks_the_object(self):
        obj = Manufacturer.objects.create(name='Acme', slug='acme')
        obj.description = 'first edit'
        gating.gate(obj, ActionChoices.UPDATE, fake_request(self.alice))

        obj.description = 'second edit'
        with self.assertRaises(ObjectLocked):
            gating.gate(obj, ActionChoices.UPDATE, fake_request(self.bob))

    def test_pending_create_also_locks_the_object(self):
        unsaved = Manufacturer(name='Acme', slug='acme')
        obj = _gate_and_save(unsaved, self.alice)

        obj.description = 'edit while still pending'
        with self.assertRaises(ObjectLocked):
            gating.gate(obj, ActionChoices.UPDATE, fake_request(self.bob))

    def test_applying_change_bypasses_gate_entirely(self):
        unsaved = Manufacturer(name='Acme', slug='acme')
        with applying_change_on():
            result = gating.gate(unsaved, ActionChoices.CREATE, fake_request(self.alice))
        self.assertIsNone(result)
        self.assertFalse(hasattr(unsaved, '_nbca_pending_create'))

    def test_approval_routing_rule_picks_approver_group(self):
        content_type = ObjectType.objects.get_for_model(Manufacturer)
        specific_group = Group.objects.create(name='Hardware Approvers')
        ApprovalRoutingRule.objects.create(
            priority=10, content_type=content_type, approver_group=specific_group,
        )

        unsaved = Manufacturer(name='Acme', slug='acme')
        obj = _gate_and_save(unsaved, self.alice)

        change_request = ChangeRequest.objects.get(object_id=obj.pk)
        self.assertEqual(change_request.approver_group, specific_group)

    @override_settings(PLUGINS_CONFIG={'netbox_change_approval': {'default_approver_group': 'Change Approvers'}})
    def test_falls_back_to_default_approver_group(self):
        unsaved = Manufacturer(name='Acme', slug='acme')
        obj = _gate_and_save(unsaved, self.alice)

        change_request = ChangeRequest.objects.get(object_id=obj.pk)
        self.assertEqual(change_request.approver_group, self.approvers)

    def test_auto_approval_rule_applies_immediately_and_untagged(self):
        content_type = ObjectType.objects.get_for_model(Manufacturer)
        AutoApprovalRule.objects.create(priority=10, content_type=content_type, action='create')

        unsaved = Manufacturer(name='Acme', slug='acme')
        obj = _gate_and_save(unsaved, self.alice)

        self.assertTrue(Manufacturer.objects.filter(slug='acme').exists())
        self.assertFalse(obj.tags.filter(slug=PENDING_TAG_SLUG).exists())  # never tagged — already decided

        change_request = ChangeRequest.objects.get(object_id=obj.pk, action=ActionChoices.CREATE)
        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertTrue(change_request.auto_approved)

    def test_auto_approval_rule_respects_action_scope(self):
        content_type = ObjectType.objects.get_for_model(Manufacturer)
        AutoApprovalRule.objects.create(priority=10, content_type=content_type, action='delete')

        unsaved = Manufacturer(name='Acme', slug='acme')
        obj = _gate_and_save(unsaved, self.alice)

        change_request = ChangeRequest.objects.get(object_id=obj.pk)
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertTrue(obj.tags.filter(slug=PENDING_TAG_SLUG).exists())

    def test_batch_scope_groups_change_requests(self):
        batch_id = uuid.uuid4()
        with batch_scope(batch_id):
            obj1 = _gate_and_save(Manufacturer(name='Acme', slug='acme'), self.alice)
            obj2 = _gate_and_save(Manufacturer(name='Globex', slug='globex'), self.alice)

        cr1 = ChangeRequest.objects.get(object_id=obj1.pk)
        cr2 = ChangeRequest.objects.get(object_id=obj2.pk)
        self.assertEqual(cr1.batch_id, batch_id)
        self.assertEqual(cr2.batch_id, batch_id)

    def test_change_request_apply_removes_tag_only(self):
        # A pending create's data is already live — approving it doesn't write
        # anything, it just removes the "Pending Approval" tag.
        unsaved = Manufacturer(name='Acme', slug='acme')
        obj = _gate_and_save(unsaved, self.alice)
        change_request = ChangeRequest.objects.get(object_id=obj.pk)

        change_request.apply(user=self.bob)

        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertEqual(change_request.decided_by, self.bob)
        obj.refresh_from_db()
        self.assertFalse(obj.tags.filter(slug=PENDING_TAG_SLUG).exists())
        self.assertTrue(Manufacturer.objects.filter(slug='acme').exists())

    def test_change_request_reject_deletes_the_object(self):
        # Unlike UPDATE/DELETE (nothing written yet, reject just closes the
        # request), a pending CREATE's object is already real — rejecting it
        # has to delete that object, or it'd be stuck un-removable forever.
        unsaved = Manufacturer(name='Acme', slug='acme')
        obj = _gate_and_save(unsaved, self.alice)
        change_request = ChangeRequest.objects.get(object_id=obj.pk)

        change_request.reject(self.bob, 'Not needed right now')

        self.assertEqual(change_request.status, StatusChoices.REJECTED)
        self.assertEqual(change_request.decision_comment, 'Not needed right now')
        self.assertFalse(Manufacturer.objects.filter(slug='acme').exists())

    def test_change_request_retract_deletes_the_object(self):
        # Same CREATE-specific handling as reject() (the object already
        # exists for real) — but a different status (RETRACTED, not
        # REJECTED), since the submitter withdrew it themselves rather than
        # an approver declining it.
        unsaved = Manufacturer(name='Acme', slug='acme')
        obj = _gate_and_save(unsaved, self.alice)
        change_request = ChangeRequest.objects.get(object_id=obj.pk)

        change_request.retract(self.alice)

        self.assertEqual(change_request.status, StatusChoices.RETRACTED)
        self.assertEqual(change_request.decided_by, self.alice)
        self.assertFalse(Manufacturer.objects.filter(slug='acme').exists())

    def test_delete_is_returned_unsaved_pending_approval(self):
        # gate() must not save a pending DELETE's ChangeRequest itself — it runs
        # inside NetBox's delete-collector transaction, which the caller is about
        # to roll back. See gating.gate()'s docstring.
        obj = Manufacturer.objects.create(name='Acme', slug='acme')

        change_request = gating.gate(obj, ActionChoices.DELETE, fake_request(self.alice))

        self.assertIsNotNone(change_request)
        self.assertIsNone(change_request.pk)
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertFalse(ChangeRequest.objects.filter(object_id=obj.pk).exists())

    def test_auto_approved_delete_logs_and_returns_none(self):
        # Unlike create/update, an auto-approved delete must not be applied via
        # ChangeRequest.apply() (that would re-delete the same row re-entrantly
        # from within its own pre_delete signal) — it's logged directly and the
        # caller is told to let the original delete proceed.
        content_type = ObjectType.objects.get_for_model(Manufacturer)
        AutoApprovalRule.objects.create(priority=10, content_type=content_type, action='delete')
        obj = Manufacturer.objects.create(name='Acme', slug='acme')

        result = gating.gate(obj, ActionChoices.DELETE, fake_request(self.alice))

        self.assertIsNone(result)
        change_request = ChangeRequest.objects.get(object_id=obj.pk, action=ActionChoices.DELETE)
        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertTrue(change_request.auto_approved)
        self.assertTrue(Manufacturer.objects.filter(pk=obj.pk).exists())  # gate() itself never deletes
