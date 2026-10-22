"""
Rules that need more than one approver, and request expiry settings.

A "Needs approval" rule says how many different people from its approver group
must approve (ApprovalRule.required_approvals) and after how many days an
undecided request expires (ApprovalRule.expire_after_days). Both are copied
onto the request when it is submitted, so editing a rule later doesn't change
requests already waiting.

Each approval is a ChangeRequestApproval row for the request's current
revision. The request is applied when the last approval needed comes in;
before that it stays pending and locked. Resubmitting after a send-back is a
new revision, so approvals start over.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .exceptions import AlreadyDecided  # noqa: F401  (re-exported: approvals.AlreadyDecided)
from .models import ActionChoices, ChangeRequest, ChangeRequestApproval, RuleOutcomeChoices, StatusChoices


def default_expiry_days():
    from .plugin_settings import get_setting

    value = get_setting('expire_pending_after_days')
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def apply_rule_settings(change_request, rule):
    """Copy the matching rule's approval count and expiry onto a new request.
    `rule` is the first matching ApprovalRule, or None (default approvers)."""
    requires = rule is not None and rule.outcome == RuleOutcomeChoices.REQUIRE
    change_request.required_approvals = max(1, rule.required_approvals or 1) if requires else 1
    days = rule.expire_after_days if requires and rule.expire_after_days is not None else default_expiry_days()
    change_request.expires_at = timezone.now() + timedelta(days=days) if days else None




class ApprovalResult:
    def __init__(self, applied, have, need):
        self.applied = applied
        self.have = have
        self.need = need


def approve(members, user):
    """
    Record `user`'s approval on every request in `members` (one request, or a
    whole bulk batch) and apply them all once each has enough approvals.

    All in one transaction: if applying fails (a conflict, a validation
    error), the approvals just recorded are rolled back too, and every request
    stays pending. Raises whatever apply() raises.
    """
    with transaction.atomic():
        # Lock the rows so two approvers clicking at once can't both see
        # "one short" and leave it unapplied, or both apply it.
        locked = list(
            ChangeRequest.objects.select_for_update().filter(
                pk__in=[cr.pk for cr in members], status=StatusChoices.PENDING,
            ).order_by('pk')
        )
        if len(locked) != len(members):
            raise AlreadyDecided('Someone else decided this request in the meantime.')

        have_counts = []
        for change_request in locked:
            ChangeRequestApproval.objects.get_or_create(
                change_request=change_request, user=user, revision=change_request.revision,
            )
            have_counts.append(change_request.current_approvals().count())

        need = max(cr.required_approvals for cr in locked)
        have = min(have_counts)
        ready = all(count >= cr.required_approvals for count, cr in zip(have_counts, locked))
        if not ready:
            _check_still_appliable(locked)
            from .notifications import partly_approved
            partly_approved(locked[0], user, have, need)
            return ApprovalResult(False, have, need)

        for change_request in locked:
            change_request.apply(user=user)
        return ApprovalResult(True, have, need)


def _check_still_appliable(members):
    """Don't count an approval for an update that can no longer be applied
    (the object changed since it was submitted); the final approval would
    fail anyway, so fail now with the same error."""
    from . import serialization
    from .exceptions import ChangeConflict

    for change_request in members:
        if change_request.action != ActionChoices.UPDATE:
            continue
        fields, missing = serialization.conflicts_for(
            change_request.content_type, change_request.object_id, change_request.diff,
        )
        if missing or fields:
            raise ChangeConflict(change_request.object_repr, fields, missing=missing)


def approvers_so_far(change_request):
    return [a.user for a in change_request.current_approvals().select_related('user') if a.user_id]
