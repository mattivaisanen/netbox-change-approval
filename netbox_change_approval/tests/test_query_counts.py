"""
Pages that list change requests must not run more database queries as the
number of pending requests grows (no per-row lookups).
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Rack, Site
from netbox.context import current_request
from users.models import Group, User

from .. import gating
from ..models import ActionChoices
from ..widgets import ApprovalsWidget
from .utils import fake_request


class QueryCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.admin = User.objects.create(username='admin', is_superuser=True)
        cls.alice = User.objects.create(username='alice')
        cls.site = Site.objects.create(name='S', slug='s')
        cls.rack = Rack.objects.create(name='R1', site=cls.site, u_height=48)
        manufacturer = Manufacturer.objects.create(name='M0', slug='m0')
        cls.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='T', slug='t', u_height=1)
        cls.role = DeviceRole.objects.create(name='R', slug='r')

    def setUp(self):
        self.added = 0

    def _add_pending(self, upto):
        """Pending edits of new manufacturers and pending new devices in the
        rack, until there are `upto` of each."""
        request = fake_request(self.alice)
        token = current_request.set(request)
        try:
            while self.added < upto:
                self.added += 1
                i = self.added
                manufacturer = Manufacturer.objects.create(name=f'Vendor {i}', slug=f'vendor-{i}')
                manufacturer.description = 'changed'
                gating.gate(manufacturer, ActionChoices.UPDATE, request)
                device = Device(
                    name=f'dev{i}', site=self.site, rack=self.rack, position=i, face='front',
                    device_type=self.device_type, role=self.role,
                )
                gating.gate(device, ActionChoices.CREATE, request)
                device.save()
        finally:
            current_request.reset(token)

    def _queries(self, fn):
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return len(ctx)

    def _page(self, url, user):
        def get():
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
        return get

    def _assert_flat(self, label, fn):
        self._add_pending(3)
        few = self._queries(fn)
        self._add_pending(20)
        many = self._queries(fn)
        self.assertLessEqual(many, few, f'{label}: {few} queries with 3 pending, {many} with 20')

    def test_approvals_queue(self):
        self._assert_flat('approvals', self._page('/plugins/change-approval/change-requests/approvals/', self.approver))

    def test_request_list(self):
        self._assert_flat('request list', self._page('/plugins/change-approval/change-requests/', self.admin))

    def test_inbox_for_approver(self):
        self._assert_flat('inbox', self._page('/plugins/change-approval/change-requests/inbox/', self.approver))

    def test_inbox_for_submitter(self):
        self._assert_flat('inbox', self._page('/plugins/change-approval/change-requests/inbox/', self.alice))

    def test_object_lists_with_pending_marks(self):
        self._assert_flat('manufacturers', self._page('/dcim/manufacturers/', self.admin))
        self._assert_flat('devices', self._page('/dcim/devices/', self.admin))

    def test_rack_page_and_elevation(self):
        self._assert_flat('rack', self._page(f'/dcim/racks/{self.rack.pk}/', self.admin))
        self._assert_flat(
            'rack elevation', self._page(f'/api/dcim/racks/{self.rack.pk}/elevation/?render=svg', self.admin),
        )

    def test_dashboard_widget(self):
        request = fake_request(self.approver)
        self._assert_flat('widget', lambda: ApprovalsWidget().render(request))

    def test_approver_still_sees_decision_buttons(self):
        self._add_pending(2)
        self.client.force_login(self.approver)
        response = self.client.get('/plugins/change-approval/change-requests/approvals/')
        self.assertContains(response, 'title="Approve"')
        self.client.force_login(self.alice)
        response = self.client.get('/plugins/change-approval/change-requests/')
        self.assertNotContains(response, 'title="Approve"')
