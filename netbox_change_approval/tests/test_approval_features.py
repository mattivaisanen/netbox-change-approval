from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import ObjectType
from dcim.models import Manufacturer
from extras.models import Notification
from users.models import Group, User

from .. import expiry, gating, inbox, notifications, permissions
from ..models import (
    ActionChoices, ApprovalRule, ChangeRequest, ChangeRequestComment, RuleOutcomeChoices, StatusChoices,
)
from .utils import fake_request


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.alice = User.objects.create(username='alice')
        cls.bob = User.objects.create(username='bob')
        cls.carol = User.objects.create(username='carol')
        for user in (cls.alice, cls.bob, cls.carol):
            user.groups.add(cls.approvers)
        cls.submitter = User.objects.create(username='submitter')

    def _rule(self, **kwargs):
        defaults = dict(
            priority=10, content_type=ObjectType.objects.get_for_model(Manufacturer),
            outcome=RuleOutcomeChoices.REQUIRE, approver_group=self.approvers,
        )
        defaults.update(kwargs)
        return ApprovalRule.objects.create(**defaults)

    def _update_request(self, name='Vendor'):
        obj = Manufacturer.objects.create(name=name, slug=name.lower())
        obj = Manufacturer.objects.get(pk=obj.pk)
        obj.description = 'changed'
        change_request = gating.gate(obj, ActionChoices.UPDATE, fake_request(self.submitter))
        self.assertIsNotNone(change_request)
        return change_request

    def _approve(self, user, change_request):
        self.client.force_login(user)
        url = reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': change_request.pk})
        return self.client.post(url, {'confirm': True})


class MultiApprovalTests(_Base):
    def test_rule_count_copied_to_request(self):
        self._rule(required_approvals=2)
        self.assertEqual(self._update_request().required_approvals, 2)

    def test_default_is_one_approval(self):
        change_request = self._update_request()
        self.assertEqual(change_request.required_approvals, 1)
        self._approve(self.alice, change_request)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)

    def test_applied_only_after_second_approver(self):
        self._rule(required_approvals=2)
        change_request = self._update_request()

        self._approve(self.alice, change_request)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(Manufacturer.objects.get(pk=change_request.object_id).description, '')

        # Same person again doesn't count.
        self._approve(self.alice, change_request)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertEqual(change_request.current_approvals().count(), 1)

        self._approve(self.bob, change_request)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.APPLIED)
        self.assertEqual(change_request.decided_by, self.bob)
        self.assertEqual(Manufacturer.objects.get(pk=change_request.object_id).description, 'changed')

    def test_approved_request_leaves_your_queue_not_others(self):
        self._rule(required_approvals=2)
        change_request = self._update_request()
        self._approve(self.alice, change_request)
        pending = ChangeRequest.objects.filter(status=StatusChoices.PENDING)
        self.assertNotIn(change_request, permissions.approver_queue(self.alice, pending))
        self.assertIn(change_request, permissions.approver_queue(self.bob, pending))
        self.assertEqual(inbox.inbox_count(self.alice), 0)

    def test_resubmission_starts_approvals_over(self):
        self._rule(required_approvals=2)
        change_request = self._update_request()
        self._approve(self.alice, change_request)
        change_request.refresh_from_db()
        change_request.send_back(self.bob, 'fix it')

        obj = Manufacturer.objects.get(pk=change_request.object_id)
        obj.description = 'changed again'
        resubmitted = gating.gate(obj, ActionChoices.UPDATE, fake_request(self.submitter))
        self.assertEqual(resubmitted.pk, change_request.pk)
        resubmitted.refresh_from_db()
        self.assertEqual(resubmitted.revision, 2)
        self.assertEqual(resubmitted.current_approvals().count(), 0)

    def test_bulk_approve_records_partial_approval(self):
        self._rule(required_approvals=2)
        change_request = self._update_request()
        self.client.force_login(self.alice)
        self.client.post(
            reverse('plugins:netbox_change_approval:changerequest_bulk_approve'),
            {'pk': [change_request.pk], '_confirm': '1'},
        )
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        self.assertTrue(change_request.has_approved(self.alice))

    def test_rule_form_refuses_more_approvals_than_members(self):
        from ..forms import ApprovalRuleForm
        form = ApprovalRuleForm(data={
            'priority': 10, 'action': 'any', 'outcome': 'require', 'approver_group': self.approvers.pk,
            'required_approvals': 4, 'enabled': True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('required_approvals', form.errors)


class CommentTests(_Base):
    def test_comment_added_and_shown(self):
        change_request = self._update_request()
        self.client.force_login(self.alice)
        url = reverse('plugins:netbox_change_approval:changerequest_comment', kwargs={'pk': change_request.pk})
        self.client.post(url, {'text': 'Why this vendor?'})
        comment = ChangeRequestComment.objects.get(change_request=change_request)
        self.assertEqual(comment.author, self.alice)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.PENDING)
        response = self.client.get(change_request.get_absolute_url())
        self.assertContains(response, 'Why this vendor?')

    def test_empty_comment_refused(self):
        change_request = self._update_request()
        self.client.force_login(self.alice)
        url = reverse('plugins:netbox_change_approval:changerequest_comment', kwargs={'pk': change_request.pk})
        self.client.post(url, {'text': ''})
        self.assertFalse(ChangeRequestComment.objects.exists())

    def test_user_who_cannot_view_cannot_comment(self):
        change_request = self._update_request()
        outsider = User.objects.create(username='outsider')
        self.client.force_login(outsider)
        url = reverse('plugins:netbox_change_approval:changerequest_comment', kwargs={'pk': change_request.pk})
        self.client.post(url, {'text': 'hi'})
        self.assertFalse(ChangeRequestComment.objects.exists())


class ExpiryTests(_Base):
    def test_rule_days_set_deadline(self):
        self._rule(expire_after_days=3)
        change_request = self._update_request()
        self.assertAlmostEqual(
            change_request.expires_at, timezone.now() + timedelta(days=3), delta=timedelta(minutes=1),
        )

    def test_no_deadline_by_default(self):
        self.assertIsNone(self._update_request().expires_at)

    def test_zero_means_never(self):
        self._rule(expire_after_days=0)
        self.assertIsNone(self._update_request().expires_at)

    def test_overdue_request_expires_and_unlocks(self):
        self._rule(expire_after_days=1)
        change_request = self._update_request()
        self.assertEqual(expiry.expire_overdue(), 0)

        self.assertEqual(expiry.expire_overdue(now=timezone.now() + timedelta(days=2)), 1)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.EXPIRED)
        self.assertEqual(Manufacturer.objects.get(pk=change_request.object_id).description, '')
        self.assertFalse(ChangeRequest.is_object_locked(change_request.content_type, change_request.object_id))
        self.assertEqual(inbox.inbox_count(self.submitter), 1)

    def test_expired_create_removes_object(self):
        self._rule(expire_after_days=1)
        obj = Manufacturer(name='New', slug='new')
        request = fake_request(self.submitter)
        gating.gate(obj, ActionChoices.CREATE, request)
        from netbox.context import current_request
        token = current_request.set(request)
        try:
            obj.save()
        finally:
            current_request.reset(token)
        change_request = ChangeRequest.objects.get(object_id=obj.pk, action=ActionChoices.CREATE)
        expiry.expire_overdue(now=timezone.now() + timedelta(days=2))
        self.assertFalse(Manufacturer.objects.filter(pk=obj.pk).exists())
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, StatusChoices.EXPIRED)


class NotificationTests(_Base):
    def _notes(self, user, change_request):
        return Notification.objects.filter(
            user=user, object_type=ContentType.objects.get_for_model(ChangeRequest), object_id=change_request.pk,
        )

    def test_new_request_notifies_approvers_not_submitter(self):
        with self.captureOnCommitCallbacks(execute=True):
            change_request = self._update_request()
        for user in (self.alice, self.bob, self.carol):
            note = self._notes(user, change_request).get()
            self.assertEqual(note.event_type, notifications.NEEDS_APPROVAL)
        self.assertFalse(self._notes(self.submitter, change_request).exists())

    def test_notification_bell_shows_our_notifications(self):
        # The bell's drop-down prints each notification's event; ours used to
        # make it fail ("__str__ returned non-string"), leaving it empty.
        with self.captureOnCommitCallbacks(execute=True):
            change_request = self._update_request()
        self.client.force_login(self.alice)
        response = self.client.get('/extras/notifications/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change request needs your approval')
        self.assertContains(response, f'#{change_request.pk} ')

    def test_decision_notifies_submitter_and_clears_approver_notes(self):
        with self.captureOnCommitCallbacks(execute=True):
            change_request = self._update_request()
        with self.captureOnCommitCallbacks(execute=True):
            self._approve(self.alice, change_request)
        self.assertEqual(self._notes(self.submitter, change_request).get().event_type, notifications.APPROVED)
        self.assertFalse(self._notes(self.bob, change_request).exists())

    def test_reject_and_send_back_notify_submitter(self):
        with self.captureOnCommitCallbacks(execute=True):
            first = self._update_request('One')
            second = self._update_request('Two')
        with self.captureOnCommitCallbacks(execute=True):
            first.reject(self.alice, 'no')
            second.send_back(self.alice, 'fix')
        self.assertEqual(self._notes(self.submitter, first).get().event_type, notifications.REJECTED)
        self.assertEqual(self._notes(self.submitter, second).get().event_type, notifications.SENT_BACK)

    def test_comment_notifies_everyone_but_author(self):
        change_request = self._update_request()
        self.client.force_login(self.alice)
        url = reverse('plugins:netbox_change_approval:changerequest_comment', kwargs={'pk': change_request.pk})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {'text': 'question'})
        self.assertEqual(self._notes(self.submitter, change_request).get().event_type, notifications.COMMENTED)
        self.assertEqual(self._notes(self.bob, change_request).get().event_type, notifications.COMMENTED)
        self.assertFalse(self._notes(self.alice, change_request).exists())

    def test_expiry_notifies_submitter(self):
        self._rule(expire_after_days=1)
        change_request = self._update_request()
        with self.captureOnCommitCallbacks(execute=True):
            expiry.expire_overdue(now=timezone.now() + timedelta(days=2))
        self.assertEqual(self._notes(self.submitter, change_request).get().event_type, notifications.EXPIRED)


class WidgetQuickViewTests(_Base):
    def test_widget_rows_have_quick_view_of_changes(self):
        from django.test import RequestFactory

        from ..widgets import ApprovalsWidget

        change_request = self._update_request()
        request = RequestFactory().get('/')
        request.user = self.alice
        html = ApprovalsWidget().render(request)
        self.assertIn(f'nbca-wq-{change_request.pk}', html)
        self.assertIn('changed', html)


class AllChangeRequestsLinkTests(_Base):
    def test_inbox_links_to_all_change_requests(self):
        self.client.force_login(self.alice)
        all_url = reverse('plugins:netbox_change_approval:changerequest_all')
        self.assertContains(self.client.get(reverse('plugins:netbox_change_approval:changerequest_inbox')), all_url)
        self.assertEqual(self.client.get(all_url).status_code, 200)

    def test_menu_link_is_not_a_prefix_of_other_pages(self):
        # NetBox highlights a menu item whose address starts the current
        # page's address; the inbox and Approvals must not light it up.
        from ..navigation import menu

        all_item = next(i for group in menu.groups for i in group.items if i.link_text == 'All Change Requests')
        url = reverse(all_item.link)
        for name in ('changerequest_inbox', 'changerequest_approvals'):
            self.assertFalse(reverse(f'plugins:netbox_change_approval:{name}').startswith(url))


class RequestPageLayoutTests(_Base):
    def test_page_shows_changes_and_only_decision_buttons(self):
        change_request = self._update_request()
        self.client.force_login(self.alice)
        html = self.client.get(change_request.get_absolute_url()).content.decode()
        self.assertIn(f'#{change_request.pk} Update', html)
        self.assertIn('>Changes<', html.replace('\n', '').replace('  ', ''))
        self.assertIn('(empty)', html)  # the description was blank before
        self.assertNotIn(f'netbox_change_approval.changerequest:{change_request.pk}', html)
        self.assertNotIn(f'/change-requests/{change_request.pk}/delete/', html)
        self.assertIn(reverse('plugins:netbox_change_approval:changerequest_approve', kwargs={'pk': change_request.pk}), html)

    def test_whole_numbers_drop_trailing_zero(self):
        from ..views import _tidy
        from decimal import Decimal

        self.assertEqual(_tidy(Decimal('10.0')), 10)
        self.assertEqual(_tidy('10.0'), '10')
        self.assertEqual(_tidy('10.5'), '10.5')
        self.assertEqual(_tidy('v1.0a'), 'v1.0a')


class NotificationDetailTests(_Base):
    def _ct(self):
        return ContentType.objects.get_for_model(ChangeRequest)

    def test_texts_say_who_did_what(self):
        with self.captureOnCommitCallbacks(execute=True):
            change_request = self._update_request()
        note = Notification.objects.get(user=self.alice, object_type=self._ct(), object_id=change_request.pk)
        self.assertIn('from submitter', note.object_repr)
        with self.captureOnCommitCallbacks(execute=True):
            change_request.reject(self.alice, 'wrong rack')
        note = Notification.objects.get(user=self.submitter, object_type=self._ct(), object_id=change_request.pk)
        self.assertIn('rejected by alice: wrong rack', note.object_repr)

    def test_comment_goes_only_to_submitter_and_approvers(self):
        outsider = User.objects.create(username='outsider', is_superuser=True)
        change_request = self._update_request()
        from ..models import ChangeRequestComment
        ChangeRequestComment.objects.create(change_request=change_request, author=outsider, text='earlier')
        self.client.force_login(self.alice)
        url = reverse('plugins:netbox_change_approval:changerequest_comment', kwargs={'pk': change_request.pk})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {'text': 'why?'})
        users = set(Notification.objects.filter(object_type=self._ct(), object_id=change_request.pk,
                                                event_type=notifications.COMMENTED).values_list('user__username', flat=True))
        self.assertEqual(users, {'submitter', 'bob', 'carol'})

    def test_partial_approval_tells_submitter(self):
        self._rule(required_approvals=2)
        change_request = self._update_request()
        with self.captureOnCommitCallbacks(execute=True):
            self._approve(self.alice, change_request)
        note = Notification.objects.get(user=self.submitter, object_type=self._ct(), object_id=change_request.pk)
        self.assertEqual(note.event_type, notifications.PARTLY_APPROVED)
        self.assertIn('1 of 2', note.object_repr)

    def test_event_fired_for_event_rules_with_full_data(self):
        from unittest import mock

        with mock.patch('extras.events.flush_events') as flush:
            with self.captureOnCommitCallbacks(execute=True):
                change_request = self._update_request()
        event = flush.call_args[0][0][0]
        self.assertEqual(event['event_type'], notifications.NEEDS_APPROVAL)
        data = event['data']
        self.assertEqual(data['id'], change_request.pk)
        self.assertEqual(data['submitted_by']['username'], 'submitter')
        self.assertEqual({p['username'] for p in data['recipients']}, {'alice', 'bob', 'carol'})
        self.assertEqual(data['changes'][0]['new'], 'changed')


class ReviewPanelTests(_Base):
    def test_policy_rules_and_reviews_on_request_page(self):
        self._rule(required_approvals=2, description='Two approvers for vendors')
        change_request = self._update_request()
        self._approve(self.alice, change_request)
        self.client.force_login(self.bob)
        html = self.client.get(change_request.get_absolute_url()).content.decode()
        self.assertIn('Policy Rules', html)
        self.assertIn('Two approvers for vendors', html)
        self.assertIn('1/2', html)
        self.assertIn('Add a Review', html)
        self.assertIn('Approved', html)

    def test_widget_links_to_inbox(self):
        from django.test import RequestFactory

        from ..widgets import ApprovalsWidget

        request = RequestFactory().get('/')
        request.user = self.submitter
        html = ApprovalsWidget().render(request)
        self.assertIn(reverse('plugins:netbox_change_approval:changerequest_inbox'), html)


class WidgetTitleLinkTests(_Base):
    def test_widget_script_links_title_to_inbox(self):
        from django.test import RequestFactory

        from ..widgets import ApprovalsWidget

        request = RequestFactory().get('/')
        request.user = self.submitter
        html = ApprovalsWidget().render(request)
        self.assertIn("closest('.grid-stack-item-content')", html)
