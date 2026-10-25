from django.test import RequestFactory
from django.urls import reverse

from ..models import ChangeRequest
from ..widgets import ApprovalsWidget
from .test_approval_features import _Base

BATCH = '00000000-0000-0000-0000-0000000000b1'


class WidgetBatchRowTests(_Base):
    """A bulk edit shows as one widget row for everyone, as in the Approval
    Inbox: approving, sending back or rejecting it decides the whole batch."""

    def setUp(self):
        self._rule()
        self.requests = [self._update_request(name) for name in ('One', 'Two', 'Three')]
        ChangeRequest.objects.filter(pk__in=[cr.pk for cr in self.requests]).update(batch_id=BATCH)

    def _render(self, user):
        request = RequestFactory().get('/')
        request.user = user
        return ApprovalsWidget().render(request)

    def _url(self, name, change_request):
        return reverse(f'plugins:netbox_change_approval:changerequest_{name}', kwargs={'pk': change_request.pk})

    def test_approver_sees_pending_batch_once(self):
        html = self._render(self.alice)
        self.assertEqual(html.count('title="Approve"'), 1)
        self.assertEqual(html.count('nbca-quick"'), 1)
        self.assertIn('3 objects', html)
        self.assertIn('>batch<', html)
        # The quick view lists every request in the batch.
        for change_request in self.requests:
            self.assertIn(change_request.get_absolute_url(), html)

    def test_sent_back_batch_is_one_row_with_revise_per_item(self):
        for change_request in self.requests:
            ChangeRequest.objects.get(pk=change_request.pk).send_back(self.alice, 'Fix it')
        html = self._render(self.submitter)
        self.assertEqual(html.count('>Sent back<'), 1)
        self.assertIn('3 objects', html)
        for change_request in self.requests:
            self.assertIn(self._url('revise', change_request), html)

    def test_single_request_keeps_its_title(self):
        single = self._update_request('Solo')
        html = self._render(self.alice)
        self.assertEqual(html.count('title="Approve"'), 2)
        self.assertIn(f'nbca-wq-{single.pk}', html)

    def test_signature_changes_when_batch_shrinks(self):
        widget = ApprovalsWidget()
        before = widget.signature(*widget._items(self.alice))
        ChangeRequest.objects.filter(pk=self.requests[-1].pk).update(batch_id=None)
        self.assertNotEqual(before, widget.signature(*widget._items(self.alice)))
