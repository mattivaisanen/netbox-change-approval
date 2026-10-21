from django.test import TestCase
from django.urls import reverse

from dcim.models import Manufacturer
from users.models import User

from .. import gating
from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import fake_request


class ChangeRequestRetractViewTests(TestCase):
    """
    Permission check is new, view-level logic (not covered by the model-level
    retract() tests in test_gating.py) — "is this your own request", a
    different check from approve/reject's "are you in the approver group".
    """
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username='alice')
        cls.bob = User.objects.create(username='bob')

        unsaved = Manufacturer(name='Acme', slug='acme')
        result = gating.gate(unsaved, ActionChoices.CREATE, fake_request(cls.alice))
        assert result is None
        unsaved.save()
        cls.change_request = ChangeRequest.objects.get(object_id=unsaved.pk)

    def test_non_submitter_is_denied(self):
        self.client.force_login(self.bob)
        url = reverse('plugins:netbox_change_approval:changerequest_retract', kwargs={'pk': self.change_request.pk})

        response = self.client.post(url, {'confirm': True})

        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, StatusChoices.PENDING)
        self.assertEqual(response.status_code, 302)

    def test_submitter_can_retract(self):
        self.client.force_login(self.alice)
        url = reverse('plugins:netbox_change_approval:changerequest_retract', kwargs={'pk': self.change_request.pk})

        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)

        post_response = self.client.post(url, {'confirm': True})

        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, StatusChoices.RETRACTED)
        self.assertEqual(post_response.status_code, 302)
        self.assertFalse(Manufacturer.objects.filter(slug='acme').exists())
