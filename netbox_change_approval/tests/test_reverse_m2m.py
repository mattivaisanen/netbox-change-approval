from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Site
from ipam.models import ASN, RIR
from users.models import Group, ObjectPermission, User

from ..models import ActionChoices, ChangeRequest, StatusChoices


class ReverseManyToManyTests(TestCase):
    """The ASN form edits "Sites", which is stored on the sites (Site.asns).
    Changing it needs approval, and approving it applies it."""

    @classmethod
    def setUpTestData(cls):
        approvers = Group.objects.create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(approvers)
        cls.user = User.objects.create(username='asn-editor')
        perm = ObjectPermission.objects.create(name='asn-test', enabled=True, actions=['view', 'change'])
        perm.object_types.set([ObjectType.objects.get_for_model(m) for m in (ASN, Site, RIR)])
        perm.users.set([cls.user])
        cls.rir = RIR.objects.create(name='RIR', slug='rir')
        cls.asn = ASN.objects.create(asn=65000, rir=cls.rir)
        cls.site = Site.objects.create(name='S1', slug='s1')

    def test_changing_asn_sites_is_queued_then_applied(self):
        self.client.force_login(self.user)
        self.client.post(reverse('ipam:asn_edit', kwargs={'pk': self.asn.pk}), {
            'asn': 65000, 'rir': self.rir.pk, 'sites': [self.site.pk],
        })

        self.assertFalse(self.site.asns.exists())
        change_request = ChangeRequest.objects.get(object_id=self.asn.pk, action=ActionChoices.UPDATE)
        self.assertEqual(change_request.diff['sites'], {'old': [], 'new': [self.site.pk]})

        self.client.force_login(self.approver)
        self.client.post(
            reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': change_request.pk}),
            {'confirm': True},
        )
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertEqual(list(self.site.asns.all()), [self.asn])
