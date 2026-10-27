from django.test import RequestFactory, TestCase
from django.urls import reverse

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Rack, Site
from core.models import ObjectType
from users.models import Group, ObjectPermission, User

from .. import amend, revision
from ..models import ChangeRequest
from ..views import _diff_rows
from ..widgets import ApprovalsWidget


class RevisedRequestDiffTests(TestCase):
    """A revised request (resubmitted after Send back, or updated while
    waiting) shows its changes, plus what changed since the revision before
    (old value struck through), on the request page and in the dashboard
    widget's quick view."""

    @classmethod
    def setUpTestData(cls):
        approvers, _ = Group.objects.get_or_create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        perm = ObjectPermission.objects.create(name='devices', actions=['view', 'add', 'change'])
        perm.object_types.set([ObjectType.objects.get_for_model(Device)])
        perm.users.set([cls.alice])
        cls.admin = User.objects.create(username='admin', is_superuser=True)
        cls.admin.groups.add(approvers)
        site = Site.objects.create(name='S1', slug='s1')
        cls.rack = Rack.objects.create(name='R1', site=site)
        manufacturer = Manufacturer.objects.create(name='M', slug='m')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Box', slug='box')
        role = DeviceRole.objects.create(name='R', slug='r')
        cls.device = Device.objects.create(
            name='sw1', site=site, rack=cls.rack, position=22, face='front', device_type=device_type, role=role,
        )

    def _move(self, position, param=None, pk=None):
        # The device's own edit form, as in the UI (Revise and Update request
        # post back to it with their parameter on the URL).
        d = self.device
        self.client.force_login(self.alice)
        url = reverse('dcim:device_edit', kwargs={'pk': d.pk}) + (f'?{param}={pk}' if param else '')
        response = self.client.post(url, {
            'name': d.name, 'device_type': d.device_type_id, 'role': d.role_id, 'site': d.site_id,
            'rack': d.rack_id, 'face': 'front', 'position': position, 'status': 'active', 'airflow': '',
        })
        self.assertEqual(response.status_code, 302, response.content.decode()[-3000:])
        return ChangeRequest.objects.get(object_id=d.pk, content_type__model='device')

    def _check(self, change_request):
        change_request = ChangeRequest.objects.get(pk=change_request.pk)
        self.assertEqual(change_request.revision, 2)
        self.assertEqual([(r['field'], str(r['new'])) for r in _diff_rows(change_request)], [('position', '26')])

        # Request page: full changes, plus the old revision's value struck through.
        self.client.force_login(self.admin)
        page = self.client.get(change_request.get_absolute_url()).content.decode()
        self.assertNotIn('No field-level changes recorded', page)
        self.assertRegex(page, r'(?s)Since revision 1.*<del class="nbca-was[^"]*">24</del>.*26')

        # Dashboard widget's quick view for the approver: the same line.
        request = RequestFactory().get('/')
        request.user = self.admin
        html = ApprovalsWidget().render(request)
        self.assertRegex(html, r'(?s)<del class="nbca-was[^"]*">24</del>.*26')

    def test_resubmit_after_send_back(self):
        first = self._move(24)
        first.send_back(self.admin, 'Use U26')
        first.refresh_from_db()
        self._check(self._move(26, revision.REVISE_PARAM, first.pk))

    def test_update_while_waiting(self):
        first = self._move(24)
        self._check(self._move(26, amend.AMEND_PARAM, first.pk))
