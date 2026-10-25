from datetime import timedelta
from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from dcim.models import Site
from extras.models import Bookmark
from users.models import User

from ..models import ChangeRequest, StatusChoices, WorkTask


class PruneChangeRequestsTests(TestCase):

    def test_deletes_only_old_finished_requests(self):
        user = User.objects.create(username='pruner')
        site = Site.objects.create(name='Prune S', slug='prune-s')
        old = timezone.now() - timedelta(days=400)

        def request(status, when, **extra):
            change_request = ChangeRequest.objects.create(
                content_type=ContentType.objects.get_for_model(Site), object_id=site.pk if status == 'pending' else None,
                action='update', status=status, object_repr='Prune S', submitted_by=user, **extra,
            )
            ChangeRequest.objects.filter(pk=change_request.pk).update(submitted_at=when, decided_at=when)
            return change_request

        old_applied = request(StatusChoices.APPLIED, old)
        Bookmark.objects.create(user=user, object=old_applied)
        old_pending = request(StatusChoices.PENDING, old)
        old_returned = request(StatusChoices.RETURNED, old)
        recent = request(StatusChoices.REJECTED, timezone.now())
        open_work = request(StatusChoices.APPLIED, old)
        WorkTask.objects.create(change_request=open_work, site=site, text='Still to do')

        call_command('prune_change_requests', '--older-than', '365', '--noinput', stdout=StringIO())

        self.assertEqual(
            set(ChangeRequest.objects.values_list('pk', flat=True)),
            {old_pending.pk, old_returned.pk, recent.pk, open_work.pk},
        )
        self.assertFalse(Bookmark.objects.exists())
