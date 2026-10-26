from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from dcim.models import Site
from users.models import User

from netbox_change_approval.api.serializers import ChangeRequestSerializer
from netbox_change_approval.models import ChangeRequest, ChangeRequestApproval, ChangeRequestComment, DeletedUser
from netbox_change_approval.reviews import reviews
from netbox_change_approval.tables import ChangeRequestTable


class DeletedUserNamesTest(TestCase):
    """Who submitted, decided, approved or commented stays visible after
    those users are deleted."""

    def setUp(self):
        self.alice = User.objects.create_user(username='alice')
        self.bob = User.objects.create_user(username='bob')
        self.site = Site.objects.create(name='Users S1', slug='users-s1')
        self.request = ChangeRequest.objects.create(
            content_type=ContentType.objects.get_for_model(Site), object_id=self.site.pk, action='update',
            status='rejected', object_repr='Users S1', submitted_by=self.alice, decided_by=self.bob,
        )
        ChangeRequestApproval.objects.create(change_request=self.request, user=self.bob, revision=1)
        ChangeRequestComment.objects.create(change_request=self.request, author=self.alice, text='please check')

    def test_names_survive_deleting_the_users(self):
        self.alice.delete()
        self.bob.delete()

        request = ChangeRequest.objects.get(pk=self.request.pk)
        self.assertIsNone(request.submitted_by)
        self.assertEqual(request.submitted_by_label, DeletedUser('alice'))
        self.assertEqual(str(request.decided_by_label), 'bob (deleted)')
        self.assertEqual(request.approvals.get().user_label, DeletedUser('bob'))
        self.assertEqual(str(request.comments.get()), 'alice (deleted): please check')
        data = ChangeRequestSerializer().get_submitted_by(request)
        self.assertEqual(data, {'id': None, 'username': 'alice', 'email': ''})
        table = ChangeRequestTable([])
        self.assertEqual(table.render_submitted_by(None, request), 'alice (deleted)')
        self.assertEqual(table.render_decided_by(None, request), 'bob (deleted)')
        self.assertIn(DeletedUser('bob'), [item['user'] for item in reviews(request)])

        admin = User.objects.create_user(username='admin', is_superuser=True)
        self.client.force_login(admin)
        page = self.client.get(request.get_absolute_url()).content.decode()
        self.assertIn('alice (deleted)', page)
        self.assertIn('bob (deleted)', page)

    def test_name_kept_for_rows_saved_without_it(self):
        ChangeRequest.objects.filter(pk=self.request.pk).update(decided_by=self.alice, decided_by_name='')
        self.alice.delete()
        self.assertEqual(ChangeRequest.objects.get(pk=self.request.pk).decided_by_label, DeletedUser('alice'))

    def test_new_user_with_the_same_name_is_someone_else(self):
        self.alice.delete()
        new_alice = User.objects.create_user(username='alice')
        request = ChangeRequest.objects.get(pk=self.request.pk)
        self.assertIsNone(request.submitted_by_id)
        self.assertEqual(str(request.submitted_by_label), 'alice (deleted)')
        self.assertFalse(ChangeRequest.objects.filter(submitted_by=new_alice).exists())
