"""
"Policy rules" and "Reviews" panels on the change request page, in the style
of NetBox's own change management: which rule the request falls under and
whether it is satisfied, who may review it, and every review so far.
"""
from django.utils.translation import gettext as _

from .models import ApprovalRule, RuleOutcomeChoices, StatusChoices


def _rule_name(change_request):
    """The approval rule this request most likely came from (rules can change
    after submission; the request keeps its own copy of what it needs)."""
    from django.db.models import Q

    rules = ApprovalRule.objects.filter(
        enabled=True, outcome=RuleOutcomeChoices.REQUIRE, approver_group_id=change_request.approver_group_id,
    ).filter(Q(content_type__isnull=True) | Q(content_type_id=change_request.content_type_id)).order_by('is_default', 'priority', 'pk')
    rule = rules.first()
    if rule is None:
        rule = ApprovalRule.get_default()
    if rule is not None:
        return str(rule), rule
    return _('Default rule'), None


def policy_rules(change_request):
    """[{'name', 'rule', 'satisfied', 'have', 'need', 'reviewers'}] (one entry
    today: a request needs one approver group)."""
    from .absence import notify_users

    if change_request.auto_approved:
        return []
    have = change_request.current_approvals().count()
    need = change_request.required_approvals or 1
    group = change_request.approver_group
    reviewers = []
    if group is not None:
        reviewers = [u for u in notify_users(group) if u.pk != change_request.submitted_by_id]
    name, rule = _rule_name(change_request)
    satisfied = change_request.status == StatusChoices.APPLIED or have >= need
    return [{
        'name': name, 'rule': rule, 'satisfied': satisfied, 'have': min(have, need), 'need': need,
        'group': group, 'reviewers': reviewers,
    }]


def reviews(change_request):
    """Every review of the request so far, oldest first:
    [{'user', 'status', 'color', 'time', 'comment', 'old_revision'}]."""
    items = []
    for approval in change_request.approvals.select_related('user').order_by('created'):
        items.append({
            'user': approval.user_label, 'status': _('Approved'), 'color': 'green', 'time': approval.created,
            'comment': '', 'old_revision': approval.revision < change_request.revision,
        })
    if change_request.revision > 1 and change_request.sent_back_by_label and change_request.sent_back_comment:
        items.append({
            'user': change_request.sent_back_by_label, 'status': _('Changes requested'), 'color': 'orange',
            'time': None, 'comment': change_request.sent_back_comment, 'old_revision': True,
        })
    decided = {
        StatusChoices.RETURNED: (_('Changes requested'), 'orange'),
        StatusChoices.REJECTED: (_('Rejected'), 'red'),
        StatusChoices.EXPIRED: (_('Expired'), 'gray'),
    }.get(change_request.status)
    if decided and not (change_request.status == StatusChoices.RETURNED
                        and change_request.decided_by_id == change_request.submitted_by_id):
        items.append({
            'user': change_request.decided_by_label, 'status': decided[0], 'color': decided[1],
            'time': change_request.decided_at, 'comment': change_request.decision_comment, 'old_revision': False,
        })
    if change_request.auto_approved:
        items.append({
            'user': None, 'status': _('Approved automatically'), 'color': 'green',
            'time': change_request.applied_at, 'comment': '', 'old_revision': False,
            'rule': change_request.auto_approved_by_rule, 'rule_name': change_request.approving_rule,
        })
    return sorted(items, key=lambda item: (item['time'] is not None, item['time'] or 0))
