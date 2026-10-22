"""
Who can see and decide change requests, CSRF on decision forms, and escaping
of user-supplied text (object names, field values, comments).
"""
from django.test import Client, TestCase

from core.models import ObjectType
from dcim.models import Manufacturer
from netbox.context import current_request
from users.models import Group, ObjectPermission, User

from .. import gating
from ..models import ActionChoices, ChangeRequest, ChangeRequestComment, StatusChoices
from .utils import fake_request

XSS = '<img src=x onerror=alert(1)>'


class ChangeRequestSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.alice = User.objects.create(username='alice')
        cls.outsider = User.objects.create(username='outsider')
        # Can view change requests in NetBox's own terms, but not manufacturers,
        # and isn't the submitter or an approver.
        cls.request_viewer = User.objects.create(username='request-viewer')
        perm = ObjectPermission.objects.create(name='view-requests', actions=['view'])
        perm.object_types.set([ObjectType.objects.get_for_model(ChangeRequest)])
        perm.users.set([cls.request_viewer])

    def setUp(self):
        vendor = Manufacturer.objects.create(name='Acme', slug='acme')
        request = fake_request(self.alice)
        token = current_request.set(request)
        try:
            vendor.description = XSS
            vendor.comments = 'secret plan'
            self.change_request = gating.gate(vendor, ActionChoices.UPDATE, request)
        finally:
            current_request.reset(token)
        ChangeRequestComment.objects.create(change_request=self.change_request, author=self.alice, text=XSS)
        self.base = self.change_request.get_absolute_url()

    def test_changelog_and_journal_tabs_follow_request_visibility(self):
        self.client.force_login(self.request_viewer)
        self.assertEqual(self.client.get(self.base).status_code, 302)  # request page: no access
        for tab in ('changelog/', 'journal/'):
            response = self.client.get(self.base + tab)
            self.assertEqual(response.status_code, 404, tab)
            self.assertNotIn('secret plan', response.content.decode())

    def test_changelog_tab_still_works_for_who_can_see_the_request(self):
        admin = User.objects.create(username='admin', is_superuser=True)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(self.base + 'changelog/').status_code, 200)

    def test_outsider_cannot_decide_or_comment(self):
        self.client.force_login(self.outsider)
        for action in ('approve/', 'reject/', 'send-back/', 'retract/'):
            self.client.post(self.base + action, {'confirm': 'true', 'comment': 'x'})
        self.client.post(self.base + 'comment/', {'text': 'sneaky'})
        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, StatusChoices.PENDING)
        self.assertFalse(self.change_request.comments.filter(text='sneaky').exists())

    def test_bulk_approve_ignores_requests_you_cannot_decide(self):
        self.client.force_login(self.outsider)
        self.client.post(
            '/plugins/change-approval/change-requests/approve/',
            {'pk': [self.change_request.pk], '_confirm': '1'},
        )
        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, StatusChoices.PENDING)

    def test_decision_forms_require_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.approver)
        for action in ('approve/', 'reject/', 'send-back/', 'comment/'):
            response = client.post(self.base + action, {'confirm': 'true', 'comment': 'x', 'text': 'x'})
            self.assertEqual(response.status_code, 403, action)
        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, StatusChoices.PENDING)

    def test_values_and_comments_are_escaped(self):
        pages = [
            (self.approver, self.base),
            (self.approver, self.base + 'approve/'),
            (self.approver, '/plugins/change-approval/change-requests/approvals/'),
            (self.approver, '/plugins/change-approval/change-requests/inbox/'),
            (self.alice, '/plugins/change-approval/change-requests/inbox/'),
        ]
        for user, url in pages:
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotIn(XSS, response.content.decode(), url)
