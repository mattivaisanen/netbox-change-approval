from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import Group, ObjectPermission, User

from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import grant_bypass


class BulkActionTests(TestCase):
    """Bulk edit, import and delete through NetBox's real views: every selected
    object that needs approval gets its own request, and none is changed."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.create(name='Change Approvers')
        cls.user = User.objects.create(username='bulk-user')
        perm = ObjectPermission.objects.create(
            name='bulk-test', enabled=True, actions=['view', 'add', 'change', 'delete'],
        )
        perm.object_types.set([ObjectType.objects.get_for_model(Manufacturer)])
        perm.users.set([cls.user])
        cls.objs = [
            Manufacturer.objects.create(name=f'M{i}', slug=f'm{i}') for i in range(3)
        ]

    def setUp(self):
        self.client.force_login(self.user)

    def _pending(self, action):
        return ChangeRequest.objects.filter(action=action, status=StatusChoices.PENDING)

    def test_bulk_edit_queues_every_object(self):
        response = self.client.post(reverse('dcim:manufacturer_bulk_edit'), {
            'pk': [o.pk for o in self.objs],
            'description': 'bulk edited',
            '_apply': '1',
        })

        self.assertEqual(response.status_code, 302)
        requests = self._pending(ActionChoices.UPDATE)
        self.assertEqual(requests.count(), 3)
        self.assertEqual(len({cr.batch_id for cr in requests}), 1)
        self.assertEqual(Manufacturer.objects.filter(description='bulk edited').count(), 0)

    def test_bulk_edit_saves_objects_that_need_no_approval(self):
        grant_bypass(self.user, Manufacturer, constraints={'slug': 'm0'})

        self.client.post(reverse('dcim:manufacturer_bulk_edit'), {
            'pk': [o.pk for o in self.objs],
            'description': 'bulk edited',
            '_apply': '1',
        })

        self.assertEqual(
            list(Manufacturer.objects.filter(description='bulk edited').values_list('slug', flat=True)),
            ['m0'],
        )
        self.assertEqual(self._pending(ActionChoices.UPDATE).count(), 2)

    def test_bulk_edit_skips_locked_object(self):
        self.client.post(reverse('dcim:manufacturer_bulk_edit'), {
            'pk': [self.objs[0].pk], 'description': 'first', '_apply': '1',
        })
        self.client.post(reverse('dcim:manufacturer_bulk_edit'), {
            'pk': [o.pk for o in self.objs], 'description': 'second', '_apply': '1',
        })

        self.assertEqual(self._pending(ActionChoices.UPDATE).count(), 3)

    def test_bulk_import_update_queues_every_record(self):
        csv = 'id,description\n' + '\n'.join(f'{o.pk},imported' for o in self.objs)
        response = self.client.post(reverse('dcim:manufacturer_bulk_import'), {
            'data': csv, 'format': 'csv', 'csv_delimiter': ',', 'import_method': 'direct',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._pending(ActionChoices.UPDATE).count(), 3)
        self.assertEqual(Manufacturer.objects.filter(description='imported').count(), 0)

    def test_bulk_delete_queues_every_object(self):
        response = self.client.post(reverse('dcim:manufacturer_bulk_delete'), {
            'pk': [o.pk for o in self.objs], 'confirm': 'true', '_confirm': '1',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._pending(ActionChoices.DELETE).count(), 3)
        self.assertEqual(Manufacturer.objects.count(), 3)
