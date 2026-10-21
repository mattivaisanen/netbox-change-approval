from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from netbox.models import NetBoxModel

from .matching import rule_matches

__all__ = (
    'ActionChoices',
    'AutoApprovalActionChoices',
    'StatusChoices',
    'ApprovalRoutingRule',
    'ApprovalRule',
    'RuleOutcomeChoices',
    'AutoApprovalRule',
    'ChangeRequest',
    'ChangeRequestApproval',
    'ChangeRequestComment',
    'ExemptModel',
)


class ActionChoices(models.TextChoices):
    # Present tense: a change request asks for something to be done
    # ("Create rack R1"), it isn't a record of what already happened.
    CREATE = 'create', _('Create')
    UPDATE = 'update', _('Update')
    DELETE = 'delete', _('Delete')


class AutoApprovalActionChoices(models.TextChoices):
    CREATE = 'create', _('Create')
    UPDATE = 'update', _('Update')
    DELETE = 'delete', _('Delete')
    ANY = 'any', _('Any')


class StatusChoices(models.TextChoices):
    PENDING = 'pending', _('Pending')
    APPLIED = 'applied', _('Applied')
    REJECTED = 'rejected', _('Rejected')
    # Distinct from REJECTED on purpose: different actor, different meaning in
    # the audit trail — the submitter changed their own mind vs. an approver
    # declining it. See ChangeRequest.retract().
    RETRACTED = 'retracted', _('Retracted')
    # An approver asked the submitter to fix something. Nothing is applied;
    # the submitter revises and resubmits (same request, revision + 1) or
    # discards it (RETRACTED). See ChangeRequest.send_back() and revision.py.
    RETURNED = 'returned', _('Sent back')
    # Nobody decided it within the rule's time limit. Ended like a rejection,
    # but by the clock, not a person. See expiry.py.
    EXPIRED = 'expired', _('Expired')


class RuleOutcomeChoices(models.TextChoices):
    REQUIRE = 'require', _('Needs approval')
    AUTO = 'auto', _('Approve automatically')
    SKIP = 'skip', _('Skip approval')


class BaseMatchRule(NetBoxModel):
    """
    Shared shape for ApprovalRoutingRule and AutoApprovalRule: both are evaluated, in
    priority order, by testing a model instance against an optional content type and
    an optional constraint (see matching.rule_matches for the constraint format).
    """
    priority = models.PositiveIntegerField(
        default=100,
        help_text=_('Rules are evaluated in ascending order; the first match wins.'),
    )
    content_type = models.ForeignKey(
        to='core.ObjectType',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_('object type'),
        help_text=_('Leave blank to match any object type.'),
    )
    constraint = models.JSONField(
        blank=True,
        null=True,
        encoder=DjangoJSONEncoder,
        help_text=_(
            "Django ORM-style filter dict, or a list of such dicts (OR'd together) — "
            "the same format used by NetBox's own object permission constraints, e.g. "
            '{"tenant__slug": "acme"}. Leave blank to match unconditionally.'
        ),
    )
    enabled = models.BooleanField(default=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        abstract = True
        ordering = ['priority', 'pk']

    def __str__(self):
        return self.description or f'{self._meta.verbose_name} #{self.pk}'

    def matches(self, instance, check_new_state=False):
        if not self.enabled:
            return False
        return rule_matches(self, instance, check_new_state=check_new_state)


class ApprovalRoutingRule(BaseMatchRule):
    """
    LEGACY: replaced by ApprovalRule (migration 0005 copied every row). Kept
    only so the old table and its change log stay readable; not used for
    gating. Remove in a later cleanup.

    Decides which Group must approve a change to a given object. The first enabled
    rule (by priority) whose content type and constraint match the object wins; if
    none match, PLUGINS_CONFIG['netbox_change_approval']['default_approver_group'] is
    used. This is how tenant/region/site-scoped approver groups work, without a
    separate concept from auto-approval rules — it's just a rule table evaluated the
    same way.
    """
    # 'users.Group', not django.contrib.auth.models.Group: NetBox ships its own
    # standalone Group model (users/models/users.py) rather than using Django's
    # stock one, and that's what User.groups / the Admin UI actually populate.
    approver_group = models.ForeignKey(
        to='users.Group',
        on_delete=models.PROTECT,
        related_name='+',
    )

    class Meta(BaseMatchRule.Meta):
        verbose_name = _('approval routing rule')
        verbose_name_plural = _('approval routing rules')

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_approval:approvalroutingrule', args=[self.pk])


class AutoApprovalRule(BaseMatchRule):
    """
    LEGACY: replaced by ApprovalRule (migration 0005 copied every row). Kept
    only so the old table and its change log stay readable; not used for
    gating. Remove in a later cleanup.

    A change matching an enabled AutoApprovalRule (content type + constraint + action)
    is applied immediately instead of being queued — a ChangeRequest row is still
    created, marked auto_approved, so there is always a full audit trail.
    """
    action = models.CharField(
        max_length=10,
        choices=AutoApprovalActionChoices.choices,
        default=AutoApprovalActionChoices.ANY,
    )

    class Meta(BaseMatchRule.Meta):
        verbose_name = _('auto-approval rule')
        verbose_name_plural = _('auto-approval rules')

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_approval:autoapprovalrule', args=[self.pk])

    def matches_action(self, action):
        return self.action == AutoApprovalActionChoices.ANY or self.action == action

    def matches(self, instance, action=None):
        if action is not None and not self.matches_action(action):
            return False
        # For an update, both the stored and the submitted values must match:
        # otherwise {"status": "planned"} would auto-approve planned -> active.
        return super().matches(instance, check_new_state=(action == AutoApprovalActionChoices.UPDATE))


class ApprovalRule(BaseMatchRule):
    """
    The one rule table (replaces ApprovalRoutingRule, AutoApprovalRule and
    ExemptModel, which migration 0005 copied into this one). Rules are checked
    in priority order and the first enabled match decides the outcome:

    - REQUIRE: the change needs approval, from `approver_group` (or the
      default approver group if blank).
    - AUTO: the change goes through and a request is logged as auto-approved.
    - SKIP: the change goes through with no record, as if the plugin weren't
      installed for that object.

    No match: needs approval from the default approver group.
    """
    action = models.CharField(
        max_length=10,
        choices=AutoApprovalActionChoices.choices,
        default=AutoApprovalActionChoices.ANY,
    )
    outcome = models.CharField(
        max_length=10,
        choices=RuleOutcomeChoices.choices,
        default=RuleOutcomeChoices.REQUIRE,
    )
    approver_group = models.ForeignKey(
        to='users.Group',
        on_delete=models.PROTECT,
        related_name='+',
        null=True,
        blank=True,
        help_text=_('Only for "Needs approval". Leave blank to use the default approver group.'),
    )
    required_approvals = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        verbose_name=_('approvals needed'),
        help_text=_(
            'Only for "Needs approval". How many different people from the approver group must '
            'approve before the change is applied.'
        ),
    )
    expire_after_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_('expire after (days)'),
        help_text=_(
            'Only for "Needs approval". A request nobody decides within this many days expires '
            'and the object is unlocked. Leave blank to use the plugin setting '
            '(expire_pending_after_days); 0 means never.'
        ),
    )

    class Meta(BaseMatchRule.Meta):
        verbose_name = _('approval rule')
        verbose_name_plural = _('approval rules')

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_approval:approvalrule', args=[self.pk])

    def get_outcome_color(self):
        return {
            RuleOutcomeChoices.REQUIRE: 'yellow',
            RuleOutcomeChoices.AUTO: 'green',
            RuleOutcomeChoices.SKIP: 'gray',
        }.get(self.outcome, 'secondary')

    def matches_action(self, action):
        return self.action == AutoApprovalActionChoices.ANY or self.action == action

    def matches(self, instance, action=None):
        if not self.enabled:
            return False
        if action is not None and not self.matches_action(action):
            return False
        # Letting a change through (AUTO/SKIP) on an update needs both the stored
        # and the submitted values to match, so {"status": "planned"} doesn't
        # wave through planned -> active. Choosing approvers looks at the
        # stored object, as routing always did.
        check_new_state = (
            action == AutoApprovalActionChoices.UPDATE and self.outcome != RuleOutcomeChoices.REQUIRE
        )
        return rule_matches(self, instance, check_new_state=check_new_state)


class ExemptModel(NetBoxModel):
    """
    LEGACY: replaced by ApprovalRule (migration 0005 copied every row). Kept
    only so the old table and its change log stay readable; not used for
    gating. Remove in a later cleanup.

    DB-backed, UI-editable list of object types excluded from gating entirely —
    complements (doesn't replace) PLUGINS_CONFIG['exempt_models']. That static
    list controls validator/signal *registration* in discovery.py at process
    start (coarse, deploy-time, needs a restart to change); this table is
    checked live, inside gating.gate(), every single time, so toggling it here
    takes effect immediately for the next request — no restart, unlike the
    config-file list. See README "Choosing which object types are gated".
    """
    content_type = models.ForeignKey(
        to='core.ObjectType',
        on_delete=models.CASCADE,
        verbose_name=_('object type'),
    )
    constraint = models.JSONField(
        blank=True,
        null=True,
        encoder=DjangoJSONEncoder,
        help_text=_(
            'Optional filter in the same format as NetBox permission constraints. '
            'Leave blank to exempt all objects of this type.'
        ),
    )
    enabled = models.BooleanField(default=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['content_type', 'pk']
        verbose_name = _('exempt model')
        verbose_name_plural = _('exempt models')

    def __str__(self):
        return self.description or str(self.content_type)

    def matches(self, instance, check_new_state=False):
        """Same matching as the rule tables. For an update, both the stored
        and the submitted values must match, so moving an object *out* of the
        exempted scope (e.g. a cable re-terminated outside Tampere) is still
        gated."""
        if not self.enabled:
            return False
        return rule_matches(self, instance, check_new_state=check_new_state)

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_approval:exemptmodel', args=[self.pk])


def combine_activity_message(existing, ours):
    """Keep any message the user typed and add ours after it, within the
    200 characters NetBox allows for an activity message."""
    if not existing:
        combined = ours
    elif ours in existing:
        combined = existing
    else:
        combined = f'{existing} | {ours}'
    return combined if len(combined) <= 200 else combined[:199] + '\u2026'


class ChangeRequest(NetBoxModel):
    """
    A single pending, applied, or rejected change to one object. Bulk actions create
    one ChangeRequest per affected object, all sharing the same `batch_id`, so a
    reject/approve decision can be applied to the whole batch at once while the
    per-object locking rule (at most one pending ChangeRequest per object) still
    makes sense unmodified.
    """
    # Must be a FK to contenttypes.ContentType specifically (not the core.ObjectType
    # proxy used elsewhere in this plugin) — Django's GenericForeignKey system check
    # (contenttypes.E004) requires it, since target_object below is a real GFK. NetBox
    # core does the same thing for its own GFKs (e.g. extras.ImageAttachment).
    content_type = models.ForeignKey(
        to='contenttypes.ContentType',
        on_delete=models.CASCADE,
        related_name='+',
    )
    object_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        help_text=_('Null until a pending "create" change is applied.'),
    )
    target_object = GenericForeignKey('content_type', 'object_id')
    object_repr = models.CharField(
        max_length=300,
        help_text=_('Display string for the target object, captured at submission time.'),
    )
    action = models.CharField(max_length=10, choices=ActionChoices.choices)
    diff = models.JSONField(
        default=dict,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text=_(
            'For create: the full set of field values. For update: '
            '{field: {"old": ..., "new": ...}} for changed fields only. For delete: empty.'
        ),
    )
    status = models.CharField(max_length=10, choices=StatusChoices.choices, default=StatusChoices.PENDING)
    auto_approved = models.BooleanField(default=False)
    batch_id = models.UUIDField(null=True, blank=True, db_index=True)

    submitted_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    approver_group = models.ForeignKey(
        to='users.Group',
        on_delete=models.PROTECT,
        null=True,
        related_name='+',
        help_text=_('Resolved at submission time from ApprovalRoutingRule / the default approver group.'),
    )

    decided_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    # Send back for revision (see revision.py).
    revision = models.PositiveSmallIntegerField(
        default=1,
        help_text=_('Goes up by one each time the submitter resubmits after a send-back.'),
    )
    sent_back_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    sent_back_comment = models.TextField(
        blank=True,
        help_text=_('What the approver asked to change the last time this was sent back.'),
    )
    # False while a rejection hasn't been opened by its submitter yet; drives
    # the inbox count. True for everything else.
    seen_by_submitter = models.BooleanField(default=True)

    # Copied from the matching rule at submission (and again on resubmission),
    # so editing a rule later doesn't change requests already waiting.
    required_approvals = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=_('approvals needed'),
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('A pending request still undecided at this time expires. Empty: never.'),
    )

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = _('change request')
        verbose_name_plural = _('change requests')
        constraints = [
            # At most one *pending* request per object — this is the object lock.
            # NULL object_id (pending creates) are not constrained against each other,
            # which is correct: distinct not-yet-created objects aren't the same "row".
            models.UniqueConstraint(
                fields=['content_type', 'object_id'],
                condition=models.Q(status=StatusChoices.PENDING),
                name='nbca_unique_pending_change_per_object',
            ),
        ]

    def __str__(self):
        return f'{self.get_action_display()} {self.object_repr} ({self.get_status_display()})'

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_approval:changerequest', args=[self.pk])

    def get_action_color(self):
        # Literal Tabler color names (green/blue/red), not Bootstrap semantic
        # ones (success/warning/danger) — matching NetBox's own
        # ObjectChangeActionChoices exactly, per "to match netbox regular style".
        return {
            ActionChoices.CREATE: 'green',
            ActionChoices.UPDATE: 'blue',
            ActionChoices.DELETE: 'red',
        }.get(self.action, 'secondary')

    def get_status_color(self):
        # Used by NetBox's ChoiceFieldColumn in lists and by the detail page badge.
        return {
            StatusChoices.PENDING: 'yellow',
            StatusChoices.APPLIED: 'green',
            StatusChoices.REJECTED: 'red',
            StatusChoices.RETRACTED: 'gray',
            StatusChoices.RETURNED: 'orange',
            StatusChoices.EXPIRED: 'gray',
        }.get(self.status, 'secondary')

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Status as loaded, so notifications.py can tell what changed on save.
        instance._nbca_loaded_status = instance.__dict__.get('status')
        return instance

    def save(self, *args, **kwargs):
        # A new pending request from the submitter of a sent-back one is that
        # request's next revision: reuse its row instead of adding another.
        if self._state.adding and self.status == StatusChoices.PENDING and not self.auto_approved:
            from .revision import adopt_sent_back
            adopt_sent_back(self)
        super().save(*args, **kwargs)

    # ---- Activity (core changelog) ------------------------------------------
    #
    # A change request is a NetBox object, so NetBox logs it in the activity
    # list like anything else. Next to the real object's own entry that made
    # every user action show up twice. So: when the real object already got an
    # entry in this same request (a pending create, an approval, a rejected
    # create, an auto-approved write), this request writes nothing of its own
    # and labels that entry's Message instead. It only logs itself when it is
    # the sole trace of what happened, e.g. an edit or delete still waiting.

    def activity_message(self):
        """One short line for the activity Message column."""
        ref = _('change request #{pk}').format(pk=self.pk)
        decider = self.decided_by or ''
        if self.status == StatusChoices.PENDING:
            text = _('Waiting for approval ({ref})') if self.revision <= 1 else _('Resubmitted for approval ({ref})')
            text = text.format(ref=ref)
        elif self.status == StatusChoices.APPLIED:
            if self.auto_approved:
                text = _('Auto-approved by rule ({ref})').format(ref=ref)
            elif decider:
                text = _('Approved by {user} ({ref})').format(user=decider, ref=ref)
            else:
                text = _('Approved ({ref})').format(ref=ref)
        elif self.status == StatusChoices.REJECTED:
            text = _('Rejected by {user} ({ref})').format(user=decider, ref=ref)
            if self.decision_comment:
                text = f'{text}: {self.decision_comment}'
        elif self.status == StatusChoices.RETURNED:
            text = _('Sent back for revision by {user} ({ref})').format(user=decider, ref=ref)
            if self.decision_comment:
                text = f'{text}: {self.decision_comment}'
        elif self.status == StatusChoices.RETRACTED:
            text = _('Withdrawn by submitter ({ref})').format(ref=ref)
        elif self.status == StatusChoices.EXPIRED:
            text = _('Expired without a decision ({ref})').format(ref=ref)
        else:
            text = ref
        return str(text)

    def _target_object_changes(self, request):
        from core.models import ObjectChange

        if request is None or self.object_id is None:
            return ObjectChange.objects.none()
        return ObjectChange.objects.filter(
            changed_object_type_id=self.content_type_id,
            changed_object_id=self.object_id,
            request_id=request.id,
        )

    def label_target_activity(self, entries=None):
        """Add this request's activity_message() to the real object's activity
        entries written in the current request."""
        from netbox.context import current_request

        if entries is None:
            entries = self._target_object_changes(current_request.get())
        message = self.activity_message()
        for entry in entries:
            entry.message = combine_activity_message(entry.message, message)
            entry.save(update_fields=['message'])

    def to_objectchange(self, action):
        from core.choices import ObjectChangeActionChoices
        from netbox.context import current_request

        message = self.activity_message()
        if action != ObjectChangeActionChoices.ACTION_DELETE:
            entries = self._target_object_changes(current_request.get())
            if getattr(self, '_nbca_log_quietly', False) or entries.exists():
                self.label_target_activity(entries)
                return None
        typed = self._changelog_message
        self._changelog_message = combine_activity_message(typed, message)
        try:
            return super().to_objectchange(action)
        finally:
            self._changelog_message = typed
    to_objectchange.alters_data = True

    @property
    def approver_names(self):
        """Human-readable "who can decide this" string, e.g. 'alice, bob (Change
        Approvers)' — used in flash messages so submitters know who to expect
        action from, not just an abstract group name."""
        if self.approver_group_id is None:
            return _('the default approver group')
        usernames = list(self.approver_group.users.order_by('username').values_list('username', flat=True))
        if not usernames:
            return self.approver_group.name
        return f'{", ".join(usernames)} ({self.approver_group.name})'

    @property
    def approver_usernames(self):
        """Like approver_names, but as a plain list — one name per line, rather
        than one joined sentence. Falls back to [group name] if the group has no
        members yet, or [] if no group could be resolved at all."""
        if self.approver_group_id is None:
            return []
        usernames = list(self.approver_group.users.order_by('username').values_list('username', flat=True))
        return usernames or [self.approver_group.name]

    @classmethod
    def is_object_locked(cls, content_type, object_id):
        if object_id is None:
            return False
        return cls.objects.filter(
            content_type=content_type, object_id=object_id, status=StatusChoices.PENDING,
        ).exists()

    @classmethod
    def batch_siblings(cls, batch_id):
        return cls.objects.filter(batch_id=batch_id) if batch_id else cls.objects.none()

    def apply(self, user=None):
        """
        Perform the real update/delete this request represents, bypassing our own
        gating (see context.applying_change_on). Called for both manual approval
        and auto-approval; `user` is None for the latter.

        CREATE is not handled by writing anything here — a pending create's
        object already exists for real (see gating.gate()'s docstring: it's
        written immediately (and marked pending in lists), precisely so it's
        visible right away and can't be duplicated). Approving one just closes
        the request.
        """
        from . import serialization
        from .context import applying_change_on

        # One transaction for the write *and* the status change: if the write
        # fails (e.g. serialization.ChangeConflict because the object changed
        # since submission), nothing is written and the request stays pending.
        with transaction.atomic(), applying_change_on():
            if self.action == ActionChoices.CREATE:
                pass  # already written when submitted; approving just closes the request
            elif self.action == ActionChoices.UPDATE:
                serialization.apply_update(self.content_type, self.object_id, self.diff)
            elif self.action == ActionChoices.DELETE:
                serialization.apply_delete(self.content_type, self.object_id)

            self.status = StatusChoices.APPLIED
            self.applied_at = timezone.now()
            if user is not None:
                self.decided_by = user
                self.decided_at = self.applied_at
            self.save()

    def reject(self, user, comment):
        """
        For UPDATE/DELETE, nothing was ever written, so rejecting just closes
        the request out. For CREATE, the object was written immediately (tagged
        marked pending — see apply()'s docstring), so rejecting it has to
        actually delete that object; otherwise a rejected request would leave a
        permanent, un-removable object behind.
        """
        if self.action == ActionChoices.CREATE and self.object_id:
            from .context import applying_change_on

            with applying_change_on():
                self.content_type.model_class().objects.filter(pk=self.object_id).delete()

        self.status = StatusChoices.REJECTED
        self.decided_by = user
        self.decided_at = timezone.now()
        self.decision_comment = comment
        self.seen_by_submitter = self.submitted_by_id == getattr(user, 'pk', None)
        self.save()

    def send_back(self, user, comment):
        """
        Ask the submitter to revise this request. Nothing is applied. For
        CREATE the object already exists (see apply()), so it is removed, the
        same as reject(); the submitter's revised form creates it again.
        """
        if self.action == ActionChoices.CREATE and self.object_id:
            from .context import applying_change_on

            with applying_change_on():
                self.content_type.model_class().objects.filter(pk=self.object_id).delete()

        self.status = StatusChoices.RETURNED
        self.decided_by = user
        self.decided_at = timezone.now()
        self.decision_comment = comment
        self.save()

    def retract(self, user):
        """
        Lets the original submitter withdraw their own still-pending request —
        no comment required, and (unlike reject()) not an approver's decision,
        hence the separate RETRACTED status rather than reusing REJECTED (same
        actor-matters reasoning as apply()'s auto_approved flag: the audit
        trail should show *who* ended this and why, not just that it ended).
        Same CREATE-is-different handling as reject(): the object already
        exists for real, so withdrawing the request has to delete it.
        """
        if self.action == ActionChoices.CREATE and self.object_id:
            from .context import applying_change_on

            with applying_change_on():
                self.content_type.model_class().objects.filter(pk=self.object_id).delete()

        self.status = StatusChoices.RETRACTED
        self.decided_by = user
        self.decided_at = timezone.now()
        self.save()

    # ---- Several approvals (ApprovalRule.required_approvals) ----------------

    def current_approvals(self):
        """Approvals given to this revision. Resubmitting starts over."""
        return self.approvals.filter(revision=self.revision)

    def has_approved(self, user):
        if user is None or not user.is_authenticated:
            return False
        return self.current_approvals().filter(user=user).exists()

    def expire(self):
        """
        Close a pending request nobody decided in time (see expiry.py). Same
        effect as a rejection, without a person: nothing is applied, and a
        pending create's object is removed.
        """
        if self.action == ActionChoices.CREATE and self.object_id:
            from .context import applying_change_on

            with applying_change_on():
                self.content_type.model_class().objects.filter(pk=self.object_id).delete()

        self.status = StatusChoices.EXPIRED
        self.decided_by = None
        self.decided_at = timezone.now()
        self.decision_comment = str(_('Expired: not decided in time.'))
        self.seen_by_submitter = False
        self.save()


class ChangeRequestApproval(models.Model):
    """One person's approval of one revision of a request, for rules that need
    more than one approver. The request is applied when the last one needed
    comes in (see approvals.approve)."""
    change_request = models.ForeignKey(
        to=ChangeRequest,
        on_delete=models.CASCADE,
        related_name='approvals',
    )
    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    revision = models.PositiveSmallIntegerField(default=1)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']
        verbose_name = _('approval')
        verbose_name_plural = _('approvals')
        constraints = [
            models.UniqueConstraint(
                fields=['change_request', 'user', 'revision'],
                name='nbca_one_approval_per_user_and_revision',
            ),
        ]

    def __str__(self):
        return f'{self.user} approved #{self.change_request_id} (revision {self.revision})'


class ChangeRequestComment(models.Model):
    """A note on a request: a question or an answer that doesn't decide
    anything. See views.ChangeRequestCommentView."""
    change_request = models.ForeignKey(
        to=ChangeRequest,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    author = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)
    text = models.TextField(max_length=2000)

    class Meta:
        ordering = ['created', 'pk']
        verbose_name = _('comment')
        verbose_name_plural = _('comments')

    def __str__(self):
        return f'{self.author}: {self.text[:50]}'
