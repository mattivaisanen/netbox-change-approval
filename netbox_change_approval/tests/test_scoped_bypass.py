from django.test import TestCase

from dcim.models import Site
from tenancy.models import Tenant
from users.models import Group, User

from .. import gating
from ..models import ActionChoices, ChangeRequest
from .utils import fake_request, grant_bypass


class ScopedBypassTests(TestCase):
    """A bypass grant scoped with constraints must only cover objects that match
    them, on creates and on the new values of an update too."""

    @classmethod
    def setUpTestData(cls):
        Group.objects.create(name='Change Approvers')
        cls.acme = Tenant.objects.create(name='Acme', slug='acme')
        cls.other = Tenant.objects.create(name='Other', slug='other')

    def setUp(self):
        # A fresh user per test: NetBox caches a user's permissions on the object.
        self.alice = User.objects.create(username=f'alice-{self._testMethodName}')
        grant_bypass(self.alice, Site, constraints={'tenant__slug': 'acme'})

    def test_create_for_matching_tenant_bypasses(self):
        site = Site(name='S1', slug='s1', tenant=self.acme)
        self.assertIsNone(gating.gate(site, ActionChoices.CREATE, fake_request(self.alice)))
        self.assertFalse(hasattr(site, '_nbca_pending_create'))

    def test_create_for_other_tenant_needs_approval(self):
        site = Site(name='S2', slug='s2', tenant=self.other)
        gating.gate(site, ActionChoices.CREATE, fake_request(self.alice))
        self.assertTrue(hasattr(site, '_nbca_pending_create'))

    def test_create_with_no_tenant_needs_approval(self):
        site = Site(name='S3', slug='s3')
        gating.gate(site, ActionChoices.CREATE, fake_request(self.alice))
        self.assertTrue(hasattr(site, '_nbca_pending_create'))

    def test_update_within_tenant_bypasses(self):
        site = Site.objects.create(name='S4', slug='s4', tenant=self.acme)
        site.description = 'edited'
        self.assertIsNone(gating.gate(site, ActionChoices.UPDATE, fake_request(self.alice)))

    def test_moving_object_to_other_tenant_needs_approval(self):
        site = Site.objects.create(name='S5', slug='s5', tenant=self.acme)
        site.tenant = self.other
        change_request = gating.gate(site, ActionChoices.UPDATE, fake_request(self.alice))
        self.assertIsInstance(change_request, ChangeRequest)

    def test_moving_object_into_scope_needs_approval(self):
        site = Site.objects.create(name='S6', slug='s6', tenant=self.other)
        site.tenant = self.acme
        change_request = gating.gate(site, ActionChoices.UPDATE, fake_request(self.alice))
        self.assertIsInstance(change_request, ChangeRequest)
