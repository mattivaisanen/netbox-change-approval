"""
Who may change approval rules: superusers only.

A rule decides whether a change needs approval at all, so anyone who can
edit rules can wave their own changes through (a "Skip approval" rule for
everything, say). NetBox's usual object permissions aren't enough on their
own: a broad grant such as "change any object type" would include the rule
tables too. So add, edit and delete on every rule table (the current one and
the legacy ones) also require a superuser, and the buttons are hidden from
everyone else. Viewing rules still follows NetBox's normal view permission.
"""
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

from netbox.tables import columns

#: ObjectAction names that only show buttons for viewing or exporting.
_READ_ONLY_ACTIONS = {'export'}


def can_manage_rules(user):
    return bool(user is not None and user.is_authenticated and user.is_active and user.is_superuser)


class RuleWriteMixin:
    """Add/edit/delete/bulk delete views of a rule table."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not can_manage_rules(request.user):
            raise PermissionDenied(_('Only superusers can change approval rules.'))
        return super().dispatch(request, *args, **kwargs)


class RuleReadMixin:
    """List and detail views of a rule table: hide the change buttons."""

    def get_permitted_actions(self, user, model=None):
        actions = super().get_permitted_actions(user, model=model)
        if can_manage_rules(user):
            return actions
        return [action for action in actions if action.name in _READ_ONLY_ACTIONS]


class RuleActionsColumn(columns.ActionsColumn):
    """Row buttons: edit/delete for superusers only; everyone keeps the
    change log button."""

    def render(self, record, table, **kwargs):
        request = getattr(table, 'context', {}).get('request')
        can_manage = can_manage_rules(getattr(request, 'user', None))
        # The Default rule can be edited but never deleted.
        undeletable = getattr(record, 'is_default', False)
        if can_manage and not undeletable:
            return super().render(record, table, **kwargs)
        keep = {'changelog', 'edit'} if can_manage else {'changelog'}
        all_actions = self.actions
        self.actions = {name: attrs for name, attrs in all_actions.items() if name in keep}
        try:
            return super().render(record, table, **kwargs)
        finally:
            self.actions = all_actions
