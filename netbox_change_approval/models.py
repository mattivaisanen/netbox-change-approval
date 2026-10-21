from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
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
    'AutoApprovalRule',
    'ChangeRequest',
    'ExemptModel',
)


class ActionChoices(models.TextChoices):
    # Past tense, matching NetBox's own core.choices.ObjectChangeActionChoices
    # (Created/Updated/Deleted) — this describes what *was done*, unlike
    # AutoApprovalActionChoices below (a rule's match criterion — "applies to
    # Create", not "this already happened"), which stays present-tense.
    CREATE = 'create', _('Created')
    UPDATE = 'update', _('Updated')
    DELETE = 'delete', _('Deleted')


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


class ExemptModel(NetBoxModel):
    """
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
        written immediately, tagged "Pending Approval", precisely so it's
        visible right away and can't be duplicated). Approving one just removes
        that tag.
        """
        from . import serialization
        from .context import applying_change_on
        from .tags import get_pending_tag

        # One transaction for the write *and* the status change: if the write
        # fails (e.g. serialization.ChangeConflict because the object changed
        # since submission), nothing is written and the request stays pending.
        with transaction.atomic(), applying_change_on():
            if self.action == ActionChoices.CREATE:
                model = self.content_type.model_class()
                model.objects.get(pk=self.object_id).tags.remove(get_pending_tag())
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
        "Pending Approval" — see apply()'s docstring), so rejecting it has to
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
