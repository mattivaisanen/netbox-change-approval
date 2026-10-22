import logging
import uuid

from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from core.models import ObjectType
from dcim.models import Manufacturer
from netbox.context import current_request
from users.models import Group, ObjectPermission, User
from utilities.request import apply_request_processors, copy_safe_request

from ..models import ActionChoices, ChangeRequest, StatusChoices


class _FakeJob:
    def __init__(self):
        self.logger = logging.getLogger('netbox_change_approval.tests.fake_job')


class BackgroundJobTests(TestCase):
    """NetBox can run bulk edit/import/delete as a background job, which calls
    the view without any middleware. Approval handling must work there too."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.create(name='Change Approvers')
        cls.user = User.objects.create(username='bg-user')
        perm = ObjectPermission.objects.create(
            name='bg-test', enabled=True, actions=['view', 'add', 'change', 'delete'],
        )
        perm.object_types.set([ObjectType.objects.get_for_model(Manufacturer)])
        perm.users.set([cls.user])
        cls.objs = [Manufacturer.objects.create(name=f'M{i}', slug=f'm{i}') for i in range(2)]

    def _run_as_job(self, url, data):
        """What NetBox's AsyncViewJob does: a copy of the request, a job
        attached, request processors applied, the view called directly."""
        original = RequestFactory().post(url, data)
        original.user = self.user
        original.id = uuid.uuid4()
        request = copy_safe_request(original)
        request.job = _FakeJob()
        view = resolve(url).func
        token = current_request.set(request)
        try:
            with apply_request_processors(request):
                view(request)
        finally:
            current_request.reset(token)

    def test_background_bulk_edit_queues_every_object(self):
        from utilities.request import NetBoxFakeRequest
        if not hasattr(NetBoxFakeRequest, 'htmx'):
            # NetBox 4.7.0's own background bulk edit fails before reaching
            # this plugin (its copied request has no `htmx`); fixed in later 4.7.x.
            self.skipTest('Background bulk edit is broken in this NetBox release')
        self._run_as_job(reverse('dcim:manufacturer_bulk_edit'), {
            'pk': [o.pk for o in self.objs], 'description': 'bg', '_apply': '1',
        })

        self.assertEqual(
            ChangeRequest.objects.filter(action=ActionChoices.UPDATE, status=StatusChoices.PENDING).count(), 2,
        )
        self.assertFalse(Manufacturer.objects.filter(description='bg').exists())

    def test_background_bulk_import_create_is_pending(self):
        self._run_as_job(reverse('dcim:manufacturer_bulk_import'), {
            'data': 'name,slug\nNew,new', 'format': 'csv', 'csv_delimiter': ',', 'import_method': 'direct',
        })

        created = Manufacturer.objects.get(slug='new')
        self.assertTrue(ChangeRequest.objects.filter(
            object_id=created.pk, action=ActionChoices.CREATE, status=StatusChoices.PENDING,
        ).exists())
