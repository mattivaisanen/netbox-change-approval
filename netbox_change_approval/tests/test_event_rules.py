from django.test import TestCase

from core.models import ObjectType
from dcim.models import Manufacturer
from extras.events import serialize_for_event
from extras.models import EventRule, Webhook
from users.models import Group, User

from .. import gating
from ..models import ActionChoices, ApprovalRule
from .utils import fake_request


class EventRuleOnChangeRequestTests(TestCase):
    """An Event Rule on Change Request (object created/updated) used to crash
    with SerializerNotFound: NetBox serializes the object for the event."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.get_or_create(name='Change Approvers')
        cls.user = User.objects.create(username='alice')

    def test_change_request_and_rule_serialize(self):
        obj = Manufacturer.objects.create(name='V', slug='v')
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'changed'
        change_request = gating.gate(obj, ActionChoices.UPDATE, fake_request(self.user))
        data = serialize_for_event(change_request)
        self.assertEqual(data['id'], change_request.pk)
        self.assertEqual(data['submitted_by']['username'], 'alice')
        self.assertEqual(data['object_type'], 'dcim.manufacturer')
        rule = ApprovalRule.objects.create(priority=1, description='r')
        self.assertEqual(serialize_for_event(rule)['description'], 'r')

    def test_event_rule_runs_without_error(self):
        from unittest import mock

        from django.contrib.contenttypes.models import ContentType

        from extras.events import EventContext, process_event_queue
        from ..models import ChangeRequest

        webhook = Webhook.objects.create(name='hook', payload_url='http://hook.test/', http_method='POST')
        rule = EventRule.objects.create(
            name='notify', event_types=['object_created', 'object_updated'],
            action_type='webhook', action_object_type=ContentType.objects.get_for_model(Webhook),
            action_object_id=webhook.pk,
        )
        rule.object_types.set([ObjectType.objects.get_for_model(ChangeRequest)])
        obj = Manufacturer.objects.create(name='W', slug='w')
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'changed'
        change_request = gating.gate(obj, ActionChoices.UPDATE, fake_request(self.user))
        event = EventContext(
            object_type=ObjectType.objects.get_for_model(ChangeRequest), object_id=change_request.pk,
            object=change_request, event_type='object_created', snapshots=None, user=self.user,
        )
        with mock.patch('extras.event_rules.get_queue') as get_queue:
            process_event_queue([event])
        self.assertTrue(get_queue.return_value.enqueue.called)
