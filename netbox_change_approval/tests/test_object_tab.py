from django.test import TestCase
from django.urls import reverse

from dcim.models import Manufacturer
from users.models import User

from .. import gating
from ..models import ActionChoices
from .utils import fake_request


class ObjectChangeRequestsTabTests(TestCase):
    """The Change Requests tab on a gated object's page."""

    def test_tab_lists_the_objects_requests(self):
        user = User.objects.create(username='tabuser', is_superuser=True)
        submitter = User.objects.create(username='tabsubmitter')
        obj = Manufacturer.objects.create(name='Tab M', slug='tab-m')
        other = Manufacturer.objects.create(name='Other M', slug='other-m')
        url = reverse('dcim:manufacturer_change_requests', kwargs={'pk': obj.pk})
        self.client.force_login(user)
        self.assertNotContains(self.client.get(obj.get_absolute_url()), url)  # hidden while empty

        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'changed'
        change_request = gating.gate(obj, ActionChoices.UPDATE, fake_request(submitter))
        other.description = 'x'
        other_request = gating.gate(other, ActionChoices.UPDATE, fake_request(submitter))

        self.assertContains(self.client.get(obj.get_absolute_url()), url)
        response = self.client.get(url)
        self.assertContains(response, change_request.get_absolute_url())
        self.assertNotContains(response, other_request.get_absolute_url())
