from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Manufacturer
from extras.models import Tag
from users.models import Group, ObjectPermission, User

from ..models import ActionChoices, ChangeRequest, StatusChoices
from .utils import grant_bypass


class TagEditTests(TestCase):
    """Adding or removing tags is a change like any other: it needs approval,
    and approving it applies it."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.user = User.objects.create(username='tag-user')
        perm = ObjectPermission.objects.create(
            name='tag-test', enabled=True, actions=['view', 'add', 'change', 'delete'],
        )
        perm.object_types.set([ObjectType.objects.get_for_model(Manufacturer), ObjectType.objects.get_for_model(Tag)])
        perm.users.set([cls.user])
        cls.red = Tag.objects.create(name='Red', slug='red')
        cls.blue = Tag.objects.create(name='Blue', slug='blue')
        cls.objs = [Manufacturer.objects.create(name=f'M{i}', slug=f'm{i}') for i in range(2)]

    def setUp(self):
        self.client.force_login(self.user)

    def _edit_tags(self, obj, tags):
        return self.client.post(reverse('dcim:manufacturer_edit', kwargs={'pk': obj.pk}), {
            'name': obj.name, 'slug': obj.slug, 'tags': [t.pk for t in tags],
        })

    def _approve(self, change_request):
        self.client.force_login(self.approver)
        response = self.client.post(
            reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': change_request.pk}),
            {'confirm': True},
        )
        self.assertEqual(response.status_code, 302)
        change_request.refresh_from_db()
        return change_request

    def test_tag_only_edit_is_queued_and_not_applied(self):
        obj = self.objs[0]
        self._edit_tags(obj, [self.red])

        self.assertEqual(list(obj.tags.all()), [])
        change_request = ChangeRequest.objects.get(action=ActionChoices.UPDATE, object_id=obj.pk)
        self.assertEqual(change_request.diff['tags'], {'old': [], 'new': [self.red.pk]})

    def test_approving_tag_edit_applies_it(self):
        obj = self.objs[0]
        obj.tags.add(self.blue)
        self._edit_tags(obj, [self.red])
        change_request = ChangeRequest.objects.get(action=ActionChoices.UPDATE, object_id=obj.pk)

        change_request = self._approve(change_request)

        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertEqual(list(obj.tags.values_list('slug', flat=True)), ['red'])

    def test_bulk_add_tags_is_queued(self):
        response = self.client.post(reverse('dcim:manufacturer_bulk_edit'), {
            'pk': [o.pk for o in self.objs], 'add_tags': [self.red.pk], '_apply': '1',
        })

        self.assertEqual(response.status_code, 302)
        for obj in self.objs:
            self.assertEqual(list(obj.tags.all()), [])
        requests = ChangeRequest.objects.filter(action=ActionChoices.UPDATE, status=StatusChoices.PENDING)
        self.assertEqual(requests.count(), 2)
        self.assertEqual(requests.first().diff['tags']['new'], [self.red.pk])

    def test_bulk_remove_tags_is_queued(self):
        for obj in self.objs:
            obj.tags.add(self.red, self.blue)

        self.client.post(reverse('dcim:manufacturer_bulk_edit'), {
            'pk': [o.pk for o in self.objs], 'remove_tags': [self.red.pk], '_apply': '1',
        })

        for obj in self.objs:
            self.assertEqual(sorted(obj.tags.values_list('slug', flat=True)), ['blue', 'red'])
        self.assertEqual(ChangeRequest.objects.filter(status=StatusChoices.PENDING).count(), 2)

    def test_bulk_add_tags_with_bypass_applies(self):
        grant_bypass(self.user, Manufacturer)

        self.client.post(reverse('dcim:manufacturer_bulk_edit'), {
            'pk': [o.pk for o in self.objs], 'add_tags': [self.red.pk], '_apply': '1',
        })

        for obj in self.objs:
            self.assertEqual(list(obj.tags.values_list('slug', flat=True)), ['red'])
        self.assertFalse(ChangeRequest.objects.exists())


class BulkAddRemoveTests(TestCase):
    """Other bulk "Add ..." / "Remove ..." fields work the same way as tags."""

    @classmethod
    def setUpTestData(cls):
        from tenancy.models import Contact, ContactGroup

        Group.objects.create(name='Change Approvers')
        cls.user = User.objects.create(username='contact-user')
        perm = ObjectPermission.objects.create(
            name='contact-test', enabled=True, actions=['view', 'change'],
        )
        perm.object_types.set([
            ObjectType.objects.get_for_model(Contact), ObjectType.objects.get_for_model(ContactGroup),
        ])
        perm.users.set([cls.user])
        cls.group = ContactGroup.objects.create(name='NOC', slug='noc')
        cls.contact = Contact.objects.create(name='Pat')

    def test_bulk_add_contact_group_is_queued(self):
        self.client.force_login(self.user)
        self.client.post(reverse('tenancy:contact_bulk_edit'), {
            'pk': [self.contact.pk], 'add_groups': [self.group.pk], '_apply': '1',
        })

        self.assertFalse(self.contact.groups.exists())
        change_request = ChangeRequest.objects.get(status=StatusChoices.PENDING)
        self.assertEqual(change_request.diff['groups'], {'old': [], 'new': [self.group.pk]})
