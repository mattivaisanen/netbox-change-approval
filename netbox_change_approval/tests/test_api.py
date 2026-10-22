from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Manufacturer
from users.models import Group, ObjectPermission, User

from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import grant_bypass


def _api_user(username):
    Group.objects.get_or_create(name='Change Approvers')
    user = User.objects.create(username=username)
    perm = ObjectPermission.objects.create(
        name=f'{username}-perm', enabled=True, actions=['view', 'add', 'change', 'delete'],
    )
    perm.object_types.set([ObjectType.objects.get_for_model(Manufacturer)])
    perm.users.set([user])
    return user


class APIMixin:
    def _send(self, method, url, data=None):
        kwargs = {'content_type': 'application/json', 'HTTP_ACCEPT': 'application/json'}
        if data is not None:
            kwargs['data'] = data
        return getattr(self.client, method)(url, **kwargs)

    def _detail_url(self, obj):
        return reverse('dcim-api:manufacturer-detail', kwargs={'pk': obj.pk})

    def _list_url(self):
        return reverse('dcim-api:manufacturer-list')


class APISingleObjectTests(APIMixin, TestCase):
    """The REST API gets JSON answers, not redirects to HTML pages."""

    @classmethod
    def setUpTestData(cls):
        cls.user = _api_user('api-user')

    def setUp(self):
        self.client.force_login(self.user)
        self.obj = Manufacturer.objects.create(name='Acme', slug='acme')

    def test_update_returns_202_with_change_request(self):
        response = self._send('patch', self._detail_url(self.obj), {'description': 'new'})

        self.assertEqual(response.status_code, 202)
        body = response.json()
        change_request = ChangeRequest.objects.get(pk=body['change_request']['id'])
        self.assertEqual(change_request.action, ActionChoices.UPDATE)
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(body['change_request']['status'], 'pending')
        self.assertEqual(
            body['change_request']['display_url'],
            f'http://testserver{change_request.get_absolute_url()}',
        )
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.description, '')

    def test_second_update_returns_409(self):
        self._send('patch', self._detail_url(self.obj), {'description': 'new'})
        response = self._send('patch', self._detail_url(self.obj), {'description': 'newer'})

        self.assertEqual(response.status_code, 409)
        self.assertIn('pending change', response.json()['detail'])

    def test_create_is_recorded_as_pending(self):
        response = self._send('post', self._list_url(), {'name': 'New', 'slug': 'new'})

        self.assertEqual(response.status_code, 201)
        created = Manufacturer.objects.get(slug='new')
        change_request = ChangeRequest.objects.get(object_id=created.pk, action=ActionChoices.CREATE)
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(change_request.submitted_by, self.user)

    def test_bulk_create_records_every_object(self):
        response = self._send('post', self._list_url(), [
            {'name': 'One', 'slug': 'one'}, {'name': 'Two', 'slug': 'two'},
        ])

        self.assertEqual(response.status_code, 201)
        for slug in ('one', 'two'):
            obj = Manufacturer.objects.get(slug=slug)
            self.assertTrue(ChangeRequest.objects.filter(
                object_id=obj.pk, action=ActionChoices.CREATE, status=StatusChoices.PENDING,
            ).exists())

    def test_bulk_update_needing_approval_fails_without_queuing(self):
        other = Manufacturer.objects.create(name='Other', slug='other')
        response = self._send('patch', self._list_url(), [
            {'id': self.obj.pk, 'description': 'x'}, {'id': other.pk, 'description': 'y'},
        ])

        self.assertEqual(response.status_code, 400)
        self.assertIn('needs approval', str(response.json()['errors']))
        self.assertFalse(ChangeRequest.objects.exists())
        self.assertFalse(Manufacturer.objects.filter(description__in=['x', 'y']).exists())

    def test_bypass_user_gets_normal_responses(self):
        grant_bypass(self.user, Manufacturer)

        response = self._send('patch', self._detail_url(self.obj), {'description': 'new'})
        self.assertEqual(response.status_code, 200)
        response = self._send('patch', self._list_url(), [{'id': self.obj.pk, 'description': 'bulk'}])
        self.assertEqual(response.status_code, 200)
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.description, 'bulk')
        self.assertFalse(ChangeRequest.objects.exists())


class APIDeleteTests(APIMixin, TransactionTestCase):
    """Deletes run in NetBox's own transaction; see GateDeleteSignalTests."""

    def setUp(self):
        self.user = _api_user('api-deleter')
        self.client.force_login(self.user)
        self.obj = Manufacturer.objects.create(name='Acme', slug='acme')

    def test_delete_returns_202_and_keeps_object(self):
        response = self._send('delete', self._detail_url(self.obj))

        self.assertEqual(response.status_code, 202)
        change_request = ChangeRequest.objects.get(pk=response.json()['change_request']['id'])
        self.assertEqual(change_request.action, ActionChoices.DELETE)
        self.assertTrue(Manufacturer.objects.filter(pk=self.obj.pk).exists())

    def test_bulk_delete_needing_approval_fails_without_queuing(self):
        response = self._send('delete', self._list_url(), [{'id': self.obj.pk}])

        self.assertEqual(response.status_code, 400)
        self.assertIn('needs approval', str(response.json()['errors']))
        self.assertFalse(ChangeRequest.objects.exists())
        self.assertTrue(Manufacturer.objects.filter(pk=self.obj.pk).exists())
