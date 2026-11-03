from django.test import TestCase
from django.utils import timezone

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import Group, ObjectPermission, User
from utilities.api import get_serializer_for_model

from .. import gating
from ..models import ActionChoices, ApprovalRule, ChangeRequest, ChangeRequestApproval
from .utils import fake_request

API = '/api/plugins/change-approval/'


def _can_view(user, model, constraints=None):
    perm = ObjectPermission.objects.create(
        name=f'view-{model._meta.model_name}', enabled=True, actions=['view'], constraints=constraints,
    )
    perm.object_types.set([ObjectType.objects.get_for_model(model)])
    perm.users.set([user])


class RestAPITests(TestCase):
    """Read-only API for dashboards: /api/plugins/change-approval/."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.get_or_create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.bob = User.objects.create(username='bob')
        cls.requests = []
        for name, user in (('A', cls.alice), ('B', cls.bob)):
            obj = Manufacturer.objects.get(pk=Manufacturer.objects.create(name=name, slug=name.lower()).pk)
            obj.description = 'changed'
            cls.requests.append(gating.gate(obj, ActionChoices.UPDATE, fake_request(user)))
        for change_request in cls.requests:
            ChangeRequestApproval.objects.create(change_request=change_request, user=cls.bob)

    def setUp(self):
        self.client.force_login(self.alice)

    def _get(self, path):
        response = self.client.get(API + path, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_change_requests_filter_by_requester_status_and_date(self):
        _can_view(self.alice, ChangeRequest)
        today = timezone.localdate().isoformat()
        data = self._get(f'change-requests/?status=pending&submitted_by_name=bob&submitted_at__gte={today}')

        self.assertEqual(data['count'], 1)
        row = data['results'][0]
        self.assertEqual(row['id'], self.requests[1].pk)
        self.assertEqual(row['status']['value'], 'pending')
        self.assertEqual(row['submitted_by']['username'], 'bob')
        self.assertEqual(row['object_type'], 'dcim.manufacturer')

    def test_approvals_follow_change_request_view_permission(self):
        _can_view(self.alice, ChangeRequest, constraints={'id': self.requests[0].pk})

        data = self._get('approvals/')

        self.assertEqual([row['change_request']['id'] for row in data['results']], [self.requests[0].pk])
        self.assertEqual(data['results'][0]['user']['username'], 'bob')

    def test_rules_list(self):
        _can_view(self.alice, ApprovalRule)
        ApprovalRule.objects.create(priority=1, description='Racks need two')

        data = self._get('rules/?enabled=true')

        self.assertIn('Racks need two', [row['description'] for row in data['results']])

    def test_change_log_can_show_change_requests_and_rules_nested(self):
        # NetBox shows the changed object of a change log entry with
        # nested=True (/api/core/object-changes/); that used to raise TypeError.
        rule = ApprovalRule.objects.create(priority=1, description='r')
        for obj in (self.requests[0], rule):
            data = get_serializer_for_model(type(obj))(obj, nested=True, context={'request': None}).data
            self.assertEqual(data['id'], obj.pk)
            self.assertNotIn('recipients', data)
