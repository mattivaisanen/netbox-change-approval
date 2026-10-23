from django.test import TestCase

from core.models import ObjectType
from dcim.models import Manufacturer
from extras.events import serialize_for_event
from extras.models import EventRule, Webhook
from users.models import Group, User

from .. import gating
from ..models import ActionChoices, ApprovalRule, ChangeRequest
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


GUIDE_BODY = '''{
  "event": {{ data.event | default(event, true) | tojson }},
  "title": {{ data.display | tojson }},
  "detail": {{ data.detail | default('', true) | tojson }},
  "link": {{ data.link | tojson }},
  "status": {{ data.status | tojson }},
  "recipients": {{ data.recipients | tojson }},
  "recipient_emails": {{ data.recipient_emails | tojson }}
}'''


class WebhookDataTests(TestCase):
    """Webhook data names who should be told and links to the request; the
    body template from docs/webhooks.md renders to valid JSON."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers, _ = Group.objects.get_or_create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice', email='alice@example.com')
        cls.bob = User.objects.create(username='bob', email='bob@example.com')
        cls.alice.groups.add(cls.approvers)
        cls.bob.groups.add(cls.approvers)
        cls.submitter = User.objects.create(username='sub', email='sub@example.com')

    def _request(self):
        obj = Manufacturer.objects.create(name='X', slug='x')
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'changed'
        return gating.gate(obj, ActionChoices.UPDATE, fake_request(self.submitter))

    def test_recipients_follow_status(self):
        change_request = self._request()
        data = serialize_for_event(change_request)
        self.assertEqual({p['username'] for p in data['recipients']}, {'alice', 'bob'})
        self.assertEqual(set(data['recipient_emails'].split(';')), {'alice@example.com', 'bob@example.com'})
        change_request.reject(self.alice, 'no')
        data = serialize_for_event(ChangeRequest.objects.get(pk=change_request.pk))
        self.assertEqual(data['recipient_emails'], 'sub@example.com')

    def test_link_uses_netbox_url(self):
        from django.test import override_settings

        change_request = self._request()
        config = {'netbox_change_approval': {'default_approver_group': 'Change Approvers', 'exempt_models': [],
                                             'netbox_url': 'https://netbox.example.com/'}}
        with override_settings(PLUGINS_CONFIG=config):
            data = serialize_for_event(change_request)
        self.assertEqual(data['link'], f'https://netbox.example.com{change_request.get_absolute_url()}')

    def test_guide_body_template_renders_json(self):
        import json

        from .. import notifications

        change_request = self._request()
        hook = Webhook(name='guide', payload_url='http://hook.test/', body_template=GUIDE_BODY)
        plugin_data = notifications.event_data(change_request, notifications.REJECTED, [self.submitter],
                                               detail='rejected by alice: "no"')
        body = json.loads(hook.render_body({'event': 'netbox_change_approval.rejected', 'data': plugin_data}))
        self.assertEqual(body['recipient_emails'], 'sub@example.com')
        self.assertEqual(body['detail'], 'rejected by alice: "no"')
        generic = json.loads(hook.render_body({'event': 'updated', 'data': serialize_for_event(change_request)}))
        self.assertEqual(generic['event'], 'updated')
        self.assertEqual(len(generic['recipients']), 2)
