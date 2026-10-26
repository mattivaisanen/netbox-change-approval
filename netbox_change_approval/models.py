import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.choices import ObjectChangeActionChoices
from netbox.models import NetBoxModel
from utilities.choices import ChoiceSet

from .matching import rule_matches

# Links to NetBox's own tables (users, groups, object types, sites, racks) use
# db_constraint=False. Django still applies on_delete while the plugin is
# installed, but once it is removed with its tables kept, the database must not
# stop NetBox from deleting those rows. repair.py clears links left dangling
# that way if the plugin is installed again.

__all__ = (
    'ActionChoices',
    'AutoApprovalActionChoices',
    'StatusChoices',
    'ApprovalRule',
    'RuleOutcomeChoices',
    'ChangeRequest',
    'ChangeRequestApproval',
    'ChangeRequestComment',
    'PluginSetting',
    'ApproverAbsence',
    'WorkTask',
    'ChangeSet',
)


class DeletedUser:
    """A deleted user, known only by the username kept on the row. Shows as
    "alice (deleted)" so it isn't mistaken for a new account with that name."""

    def __init__(self, username):
        self.username = username

    def get_username(self):
        return self.username

    def __str__(self):
        return str(_('{name} (deleted)')).format(name=self.username)

    def __eq__(self, other):
        return isinstance(other, DeletedUser) and other.username == self.username

    def __hash__(self):
        return hash(self.username)


class UserLabel:
    """`<field>_label`: the linked user, or once that user is deleted a
    DeletedUser with the username kept in `<field>_name`."""

    def __set_name__(self, owner, name):
        self.field = name.removesuffix('_label')

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        user = getattr(instance, self.field)
        if user is not None:
            return user
        name = getattr(instance, f'{self.field}_name')
        return DeletedUser(name) if name else None


class KeepsUserNames:
    """Copies the username of each field in `user_fields` into `<field>_name`
    on save, so a request still says who submitted, decided or commented after
    that user is deleted (signals.keep_names_of_deleted_user covers rows saved
    without it, e.g. by queryset.update())."""
    user_fields = ()

    def copy_user_names(self):
        for name in self.user_fields:
            field = self._meta.get_field(name)
            if field.is_cached(self):
                user = getattr(self, name)
                if user is not None:
                    setattr(self, f'{name}_name', user.get_username())
            elif getattr(self, field.attname) and not getattr(self, f'{name}_name'):
                username = field.related_model._base_manager.filter(
                    pk=getattr(self, field.attname),
                ).values_list('username', flat=True).first()
                setattr(self, f'{name}_name', username or '')

    def save(self, *args, **kwargs):
        self.copy_user_names()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {
                f'{name}_name' for name in self.user_fields
                if name in update_fields or f'{name}_id' in update_fields
            }
        super().save(*args, **kwargs)


def _username_field():
    return models.CharField(max_length=150, blank=True, editable=False)


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


class ChangeRequestStatusChoices(ChoiceSet):
    """Badge colors of StatusChoices, as a NetBox ChoiceSet so they follow
    NetBox's color handling: a theme plugin that recolors statuses (keyed by
    value, like "pending" or "applied") recolors these too."""
    CHOICES = [
        (StatusChoices.PENDING.value, StatusChoices.PENDING.label, 'yellow'),
        (StatusChoices.APPLIED.value, StatusChoices.APPLIED.label, 'green'),
        (StatusChoices.REJECTED.value, StatusChoices.REJECTED.label, 'red'),
        (StatusChoices.RETRACTED.value, StatusChoices.RETRACTED.label, 'gray'),
        (StatusChoices.RETURNED.value, StatusChoices.RETURNED.label, 'orange'),
        (StatusChoices.EXPIRED.value, StatusChoices.EXPIRED.label, 'gray'),
    ]


class ChangeSetStatusChoices(ChoiceSet):
    """Badge colors of ChangeSet.status, for the same reason."""
    CHOICES = [
        ('draft', _('Draft'), 'gray'),
        ('submitted', _('Submitted'), 'yellow'),
    ]


class RuleOutcomeChoices(models.TextChoices):
    REQUIRE = 'require', _('Needs approval')
    AUTO = 'auto', _('Approve automatically')
    SKIP = 'skip', _('Skip approval')


class BaseMatchRule(NetBoxModel):
    """
    Fields shared by rule tables: a rule is evaluated, in priority order, by
    testing a model instance against an optional content type and an optional
    constraint (see matching.rule_matches for the constraint format).
    """
    priority = models.PositiveIntegerField(
        default=100,
        help_text=_('Rules are evaluated in ascending order; the first match wins.'),
    )
    content_type = models.ForeignKey(
        to='core.ObjectType', db_constraint=False,
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


class ApprovalRule(BaseMatchRule):
    """
    The one rule table (it replaced three older ones; migration 0005 copied
    their rows here and 0008 dropped them). Rules are checked
    in priority order and the first enabled match decides the outcome:

    - REQUIRE: the change needs approval, from `approver_group` (or the
      default approver group if blank).
    - AUTO: the change goes through and a request is logged as auto-approved.
    - SKIP: the change goes through with no record, as if the plugin weren't
      installed for that object.

    The Default rule (is_default) is one row that always sits at the bottom
    of the list: it decides every change no other rule matches. It always
    means "Needs approval", can't be deleted or disabled, and its approver
    group and expiry are also what "Needs approval" rules above it use when
    they leave those blank. Migration 0017 creates it.
    """
    #: Priority the Default rule is stored with, so sorting by priority still
    #: puts it last. Ordering by is_default does the real work.
    DEFAULT_PRIORITY = 2147483647

    is_default = models.BooleanField(default=False, editable=False)
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
        to='users.Group', db_constraint=False,
        on_delete=models.PROTECT,
        related_name='+',
        null=True,
        blank=True,
        help_text=_('Only for "Needs approval". Leave blank to use the Default rule\'s approver group.'),
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
    require_reason = models.BooleanField(
        default=False,
        verbose_name=_('reason required'),
        help_text=_(
            'Only for "Needs approval". Refuse the change until the changelog message (the reason, '
            'e.g. a ticket number) is filled in.'
        ),
    )
    expire_after_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_('expire after (days)'),
        help_text=_(
            'Only for "Needs approval". A request nobody decides within this many days expires '
            'and the object is unlocked. Leave blank to use the Default rule\'s expiry; 0 means never.'
        ),
    )

    class Meta(BaseMatchRule.Meta):
        ordering = ['is_default', 'priority', 'pk']
        verbose_name = _('approval rule')
        verbose_name_plural = _('approval rules')
        constraints = [
            models.UniqueConstraint(
                fields=['is_default'], condition=models.Q(is_default=True),
                name='netbox_change_approval_one_default_rule',
            ),
        ]

    def __str__(self):
        if self.is_default and not self.description:
            return str(_('Default rule'))
        return super().__str__()

    @classmethod
    def get_default(cls):
        """The Default rule. Migration 0017 creates it; if it's missing anyway
        (a database flush, say), it's created again from configuration.py."""
        from django.db import IntegrityError

        rule = cls.objects.filter(is_default=True).select_related('approver_group').first()
        if rule is not None:
            return rule
        from users.models import Group

        from .plugin_settings import config_value

        days = config_value('expire_pending_after_days')
        try:
            days = int(days) if days else None
        except (TypeError, ValueError):
            days = None
        try:
            with transaction.atomic():
                return cls.objects.create(
                    is_default=True, priority=cls.DEFAULT_PRIORITY, outcome=RuleOutcomeChoices.REQUIRE,
                    action=AutoApprovalActionChoices.ANY, enabled=True, description=str(_('Default rule')),
                    approver_group=Group.objects.filter(name=config_value('default_approver_group')).first(),
                    expire_after_days=days,
                )
        except IntegrityError:  # another process created it first
            return cls.objects.filter(is_default=True).select_related('approver_group').first()

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_approval:approvalrule', args=[self.pk])

    def get_outcome_color(self):
        return {
            RuleOutcomeChoices.REQUIRE: 'warning',
            RuleOutcomeChoices.AUTO: 'success',
            RuleOutcomeChoices.SKIP: 'secondary',
        }.get(self.outcome, 'secondary')

    def matches_action(self, action):
        return self.action == AutoApprovalActionChoices.ANY or self.action == action

    def matches(self, instance, action=None):
        if self.is_default:
            return True
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


class ChangeRequest(KeepsUserNames, NetBoxModel):
    """
    A single pending, applied, or rejected change to one object. Bulk actions create
    one ChangeRequest per affected object, all sharing the same `batch_id`, so a
    reject/approve decision can be applied to the whole batch at once while the
    per-object locking rule (at most one pending ChangeRequest per object) still
    makes sense unmodified.
    """
    user_fields = ('submitted_by', 'decided_by', 'sent_back_by', 'scheduled_by')

    # Must be a FK to contenttypes.ContentType specifically (not the core.ObjectType
    # proxy used elsewhere in this plugin) — Django's GenericForeignKey system check
    # (contenttypes.E004) requires it, since target_object below is a real GFK. NetBox
    # core does the same thing for its own GFKs (e.g. extras.ImageAttachment).
    content_type = models.ForeignKey(
        to='contenttypes.ContentType', db_constraint=False,
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
    # The rule that auto-approved it, shown as its approver. The name is kept
    # too, so the request still says which rule it was after that rule is
    # renamed or deleted.
    auto_approved_by_rule = models.ForeignKey(
        to='netbox_change_approval.ApprovalRule',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    auto_approved_by_rule_name = models.CharField(max_length=200, blank=True)
    batch_id = models.UUIDField(null=True, blank=True, db_index=True)

    submitted_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    submitted_by_name = _username_field()
    submitted_by_label = UserLabel()
    submitted_at = models.DateTimeField(auto_now_add=True)
    # Requests that are decided don't hold on to their group: it can be
    # deleted, and approver_group_name keeps showing who it was. Open requests
    # do (signals.protect_open_requests_group).
    approver_group = models.ForeignKey(
        to='users.Group', db_constraint=False,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
        help_text=_('Resolved at submission time from ApprovalRoutingRule / the default approver group.'),
    )
    approver_group_name = models.CharField(max_length=150, blank=True, editable=False)

    decided_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    decided_by_name = _username_field()
    decided_by_label = UserLabel()
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    # Send back for revision (see revision.py).
    revision = models.PositiveSmallIntegerField(
        default=1,
        help_text=_('Goes up by one each time the submitter resubmits after a send-back.'),
    )
    sent_back_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    sent_back_by_name = _username_field()
    sent_back_by_label = UserLabel()
    sent_back_comment = models.TextField(
        blank=True,
        help_text=_('What the approver asked to change the last time this was sent back.'),
    )
    # False while a rejection hasn't been opened by its submitter yet; drives
    # the inbox count. True for everything else.
    seen_by_submitter = models.BooleanField(default=True)

    reason = models.CharField(
        max_length=200,
        blank=True,
        help_text=_('Why: the changelog message typed with the change (reasons.py).'),
    )

    # Approved, to be applied at a chosen time (scheduling.py). Still pending
    # (and the object locked) until then.
    scheduled_for = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('Fully approved; applied automatically at this time.'),
    )
    scheduled_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    scheduled_by_name = _username_field()
    scheduled_by_label = UserLabel()

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
        indexes = [
            # An object's requests: its Change Requests tab (object_tab.py).
            models.Index(fields=['content_type', 'object_id', 'status'], name='nbca_cr_object'),
        ]
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

    @property
    def approving_rule(self):
        """Name of the rule that auto-approved this request ('' if none, or
        if it was logged before rule names were recorded)."""
        if not self.auto_approved:
            return ''
        return self.auto_approved_by_rule_name or (str(self.auto_approved_by_rule) if self.auto_approved_by_rule_id else '')

    def get_action_color(self):
        # The changelog's own colors (green/blue/red), read from NetBox's
        # ObjectChangeActionChoices (same create/update/delete values) so a
        # theme that recolors the changelog recolors change requests too.
        return ObjectChangeActionChoices.colors.get(self.action, 'secondary')

    def get_status_color(self):
        # Used by NetBox's ChoiceFieldColumn in lists, the detail page badge
        # and the widget. Through the ChoiceSet so themes can recolor it.
        return ChangeRequestStatusChoices.colors.get(self.status, 'secondary')

    @classmethod
    def from_db(cls, db, field_names, values, *args, **kwargs):
        # Pass through whatever else Django adds (Django 6.1: fetch_mode).
        instance = super().from_db(db, field_names, values, *args, **kwargs)
        # Status as loaded, so notifications.py can tell what changed on save.
        instance._nbca_loaded_status = instance.__dict__.get('status')
        return instance

    def save(self, *args, **kwargs):
        # A new pending request from the submitter of a sent-back one is that
        # request's next revision: reuse its row instead of adding another.
        if self._state.adding and self.status == StatusChoices.PENDING and not self.auto_approved:
            from .revision import adopt_sent_back
            adopt_sent_back(self)
        if self.approver_group_id and self.approver_group.name != self.approver_group_name:
            self.approver_group_name = self.approver_group.name
        super().save(*args, **kwargs)

    @property
    def approver_group_label(self):
        """The approver group's name, also once the group has been deleted."""
        if self.approver_group_id:
            return self.approver_group.name
        return self.approver_group_name

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
            if self.auto_approved and self.approving_rule:
                text = _('Auto-approved by rule {rule} ({ref})').format(rule=self.approving_rule, ref=ref)
            elif self.auto_approved:
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
            fields = ['message'] + self._attribute_to_submitter(entry)
            entry.save(update_fields=fields)

    def _attribute_to_submitter(self, entry):
        """
        A decision (approved, rejected, sent back, expired) writes its activity
        entry in the approver's request, so NetBox names the approver as its
        user. The change is the submitter's, and the message already names
        who decided ("Approved by matti"), so the entry's user is set to the
        submitter. Returns the fields changed.
        """
        if self.status == StatusChoices.PENDING or self.submitted_by_id is None:
            return []
        if entry.user_id == self.submitted_by_id:
            return []
        entry.user = self.submitted_by
        entry.user_name = self.submitted_by.username
        return ['user', 'user_name']

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
            entry = super().to_objectchange(action)
        finally:
            self._changelog_message = typed
        if entry is not None and self.status != StatusChoices.PENDING and self.submitted_by_id:
            # NetBox sets the entry's user after this returns; see
            # _attribute_change_to_submitter below.
            entry._nbca_submitter = self.submitted_by
        return entry
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
                # Already written when submitted; approving just closes the
                # request. Unless the object was deleted since (by a bypass
                # user, say): then there's nothing left to approve.
                model = self.content_type.model_class()
                if model is not None and not model.objects.filter(pk=self.object_id).exists():
                    from .exceptions import ChangeConflict
                    raise ChangeConflict(self.object_repr, [], missing=True)
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
        with transaction.atomic():
            self._claim()
            if self.action == ActionChoices.CREATE and self.object_id:
                from .context import applying_change_on

                with applying_change_on():
                    self._remove_created_object()

            self.status = StatusChoices.REJECTED
            self.decided_by = user
            self.decided_at = timezone.now()
            self.decision_comment = comment
            self.seen_by_submitter = self.submitted_by_id == getattr(user, 'pk', None)
            self.save()
            self._discard_held_uploads()

    def _remove_created_object(self):
        """Delete the object a pending create wrote (rejected, sent back,
        withdrawn, expired). Raises CreatedObjectInUse when other objects
        depend on it through protected links (a device in a new rack)."""
        from django.db.models import ProtectedError, RestrictedError

        from .exceptions import CreatedObjectInUse

        try:
            self.content_type.model_class().objects.filter(pk=self.object_id).delete()
        except ProtectedError as e:
            raise CreatedObjectInUse(self.object_repr, e.protected_objects)
        except RestrictedError as e:
            raise CreatedObjectInUse(self.object_repr, e.restricted_objects)

    def send_back(self, user, comment):
        """
        Ask the submitter to revise this request. Nothing is applied. For
        CREATE the object already exists (see apply()), so it is removed, the
        same as reject(); the submitter's revised form creates it again.
        """
        with transaction.atomic():
            self._claim()
            if self.action == ActionChoices.CREATE and self.object_id:
                from .context import applying_change_on

                with applying_change_on():
                    self._remove_created_object()

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
        with transaction.atomic():
            self._claim(StatusChoices.PENDING, StatusChoices.RETURNED)
            if self.action == ActionChoices.CREATE and self.object_id:
                from .context import applying_change_on

                with applying_change_on():
                    self._remove_created_object()

            self.status = StatusChoices.RETRACTED
            self.decided_by = user
            self.decided_at = timezone.now()
            self.save()
            self._discard_held_uploads()

    def _claim(self, *allowed):
        """
        Inside a transaction: lock this request's row and make sure it is
        still open (pending, unless `allowed` says otherwise), then reload it.
        Without this, rejecting at the same moment someone else approved could
        mark an applied change as rejected (and, for a new object, delete
        it). Raises AlreadyDecided.
        """
        from .exceptions import AlreadyDecided

        allowed = allowed or (StatusChoices.PENDING,)
        status = type(self).objects.select_for_update().filter(pk=self.pk).values_list('status', flat=True).first()
        if status not in allowed:
            raise AlreadyDecided(str(_('Request #{pk} was already decided or withdrawn.')).format(pk=self.pk))
        self.refresh_from_db()

    def _discard_held_uploads(self, keep=()):
        """Files uploaded in this held edit won't be used now; delete them
        (see serialization.discard_held_uploads)."""
        if self.action != ActionChoices.UPDATE:
            return
        from .serialization import discard_held_uploads
        content_type, object_id, diff = self.content_type, self.object_id, self.diff
        # Only once the decision is saved for good: a rolled-back decision
        # must not lose the file.
        transaction.on_commit(lambda: discard_held_uploads(content_type, object_id, diff, keep=keep))

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
        with transaction.atomic():
            self._claim()
            if self.action == ActionChoices.CREATE and self.object_id:
                from .context import applying_change_on

                with applying_change_on():
                    self._remove_created_object()

            self.status = StatusChoices.EXPIRED
            self.decided_by = None
            self.decided_by_name = ''
            self.decided_at = timezone.now()
            self.decision_comment = str(_('Expired: not decided in time.'))
            self.seen_by_submitter = False
            self.save()
            self._discard_held_uploads()


class ChangeRequestApproval(KeepsUserNames, models.Model):
    """One person's approval of one revision of a request, for rules that need
    more than one approver. The request is applied when the last one needed
    comes in (see approvals.approve)."""
    user_fields = ('user',)

    change_request = models.ForeignKey(
        to=ChangeRequest,
        on_delete=models.CASCADE,
        related_name='approvals',
    )
    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    user_name = _username_field()
    user_label = UserLabel()
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


class ChangeRequestComment(KeepsUserNames, models.Model):
    """A note on a request: a question or an answer that doesn't decide
    anything. See views.ChangeRequestCommentView."""
    user_fields = ('author',)

    change_request = models.ForeignKey(
        to=ChangeRequest,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    author = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    author_name = _username_field()
    author_label = UserLabel()
    created = models.DateTimeField(auto_now_add=True)
    text = models.TextField(max_length=2000)

    class Meta:
        ordering = ['created', 'pk']
        verbose_name = _('comment')
        verbose_name_plural = _('comments')

    def __str__(self):
        return f'{self.author_label}: {self.text[:50]}'


class ApproverAbsence(models.Model):
    """
    "I'm away; <stand_in> decides for me." While active, the stand-in can
    approve, reject and send back requests routed to the away approver's
    groups, sees them in their queue and inbox, and gets the notifications
    instead of them. See absence.py.
    """
    user = models.OneToOneField(
        to=settings.AUTH_USER_MODEL, db_constraint=False,
        on_delete=models.CASCADE,
        related_name='+',
    )
    stand_in = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False,
        on_delete=models.CASCADE,
        related_name='+',
        verbose_name=_('stand-in'),
    )
    starts = models.DateField(verbose_name=_('from'))
    ends = models.DateField(null=True, blank=True, verbose_name=_('until'), help_text=_('Last day away. Empty: until cleared.'))
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = _('approver absence')
        verbose_name_plural = _('approver absences')

    def __str__(self):
        return f'{self.user} away, {self.stand_in} stands in'

    def is_active(self, today=None):
        today = today or timezone.localdate()
        return self.starts <= today and (self.ends is None or today <= self.ends)


class WorkTask(KeepsUserNames, models.Model):
    """
    One physical job on the field work sheet ("Mount sw1 in A3 U20-21"),
    written for a request while it waits and shown once the request is
    applied. See worksheet.py.
    """
    user_fields = ('done_by',)

    change_request = models.ForeignKey(
        to=ChangeRequest,
        on_delete=models.CASCADE,
        related_name='work_tasks',
    )
    site = models.ForeignKey(to='dcim.Site', db_constraint=False, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    rack = models.ForeignKey(to='dcim.Rack', db_constraint=False, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    text = models.CharField(max_length=300)
    links = models.JSONField(
        default=list, blank=True,
        help_text=_('Names in the text to link, as [text, url] pairs in order.'),
    )
    done_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    done_by_name = _username_field()
    done_by_label = UserLabel()
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['pk']
        verbose_name = _('work task')
        verbose_name_plural = _('work tasks')

    def __str__(self):
        return self.text


class ChangeSet(KeepsUserNames, models.Model):
    """
    A named bundle of changes ("Install sw1 in rack A3") its author collects
    while working, then submits to be decided as one. Its requests share its
    batch_id, so they are approved or rejected together like a bulk edit.
    See changesets.py.
    """
    DRAFT = 'draft'
    SUBMITTED = 'submitted'

    user_fields = ('created_by',)

    name = models.CharField(max_length=100)
    ticket = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False, on_delete=models.SET_NULL, null=True, related_name='+',
    )
    created_by_name = _username_field()
    created_by_label = UserLabel()
    created = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=10, default=DRAFT, choices=((DRAFT, _('Draft')), (SUBMITTED, _('Submitted'))),
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    batch_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)

    class Meta:
        ordering = ['-created']
        verbose_name = _('change set')
        verbose_name_plural = _('change sets')

    def __str__(self):
        return self.name

    def get_status_color(self):
        return ChangeSetStatusChoices.colors.get(self.status, 'secondary')

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_approval:changeset', args=[self.pk])

    def requests(self):
        return ChangeRequest.objects.filter(batch_id=self.batch_id)


class PluginSetting(KeepsUserNames, models.Model):
    """A plugin setting saved on the settings page, overriding configuration.py
    until reset. See plugin_settings.py."""
    user_fields = ('updated_by',)

    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(null=True, blank=True)
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, db_constraint=False, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    updated_by_name = _username_field()
    updated_by_label = UserLabel()

    class Meta:
        ordering = ['key']
        verbose_name = _('plugin setting')
        verbose_name_plural = _('plugin settings')

    def __str__(self):
        return self.key


def _attribute_change_to_submitter(sender, instance, **kwargs):
    """A change request's own activity entry for a decision names the
    submitter as its user (see ChangeRequest._attribute_to_submitter)."""
    submitter = getattr(instance, '_nbca_submitter', None)
    if submitter is not None:
        instance.user = submitter
        instance.user_name = submitter.username


models.signals.pre_save.connect(
    _attribute_change_to_submitter, sender='core.ObjectChange',
    dispatch_uid='netbox_change_approval:attribute_change_to_submitter',
)
