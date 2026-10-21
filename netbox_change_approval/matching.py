"""
Shared constraint-matching engine used by both ApprovalRoutingRule and
AutoApprovalRule (see models.py). A rule's `constraint` field uses the exact same
shape as NetBox's own ObjectPermission.constraints: a Django ORM filter dict, or a
list of such dicts which are OR'd together. An empty/null constraint matches
unconditionally. This is a deliberate reuse of a format NetBox admins already know
from the stock Permissions UI, rather than a plugin-specific rule language.

For a *saved* instance (updates, deletes) matching is delegated straight to the
database via the same utilities.permissions.qs_filter_from_constraints() helper
NetBox's own permission backend uses, so behavior (including relationship lookups)
is identical.

For an *unsaved* instance (a pending create — there is no database row yet) we fall
back to a best-effort in-memory evaluator that walks attribute/relation chains on
the instance itself. It supports the common comparison lookups but not arbitrary
queryset semantics (e.g. it cannot evaluate a constraint against a related object
that hasn't been assigned on the in-memory instance yet). This is a documented
limitation — see README.md "Auto-approval and routing rules on new objects".
"""
from utilities.permissions import qs_filter_from_constraints

__all__ = ('rule_matches',)

_LOOKUP_SUFFIXES = {
    'exact', 'iexact', 'in', 'isnull', 'gt', 'gte', 'lt', 'lte',
    'contains', 'icontains', 'startswith', 'istartswith', 'endswith', 'iendswith',
}


def _normalize_constraints(constraint):
    """Return a list of filter dicts to OR together. Empty/null means 'match anything'."""
    if not constraint:
        return [{}]
    if isinstance(constraint, list):
        return constraint or [{}]
    return [constraint]


def _split_lookup(path):
    parts = path.split('__')
    if len(parts) > 1 and parts[-1] in _LOOKUP_SUFFIXES:
        return parts[:-1], parts[-1]
    return parts, 'exact'


def _resolve_attr_chain(obj, parts):
    for part in parts:
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def _apply_lookup(value, lookup, target):
    if lookup == 'exact':
        return value == target
    if lookup == 'iexact':
        return str(value).lower() == str(target).lower()
    if lookup == 'in':
        return value in target
    if lookup == 'isnull':
        return (value is None) == bool(target)
    if lookup in ('gt', 'gte', 'lt', 'lte'):
        if value is None:
            return False
        return {
            'gt': value > target, 'gte': value >= target,
            'lt': value < target, 'lte': value <= target,
        }[lookup]
    text = '' if value is None else str(value)
    if lookup == 'contains':
        return str(target) in text
    if lookup == 'icontains':
        return str(target).lower() in text.lower()
    if lookup == 'startswith':
        return text.startswith(str(target))
    if lookup == 'istartswith':
        return text.lower().startswith(str(target).lower())
    if lookup == 'endswith':
        return text.endswith(str(target))
    if lookup == 'iendswith':
        return text.lower().endswith(str(target).lower())
    raise ValueError(
        f"Unsupported lookup '{lookup}' for in-memory constraint matching on an unsaved instance"
    )


def _matches_in_memory(instance, constraint_dict):
    for path, target in constraint_dict.items():
        parts, lookup = _split_lookup(path)
        value = _resolve_attr_chain(instance, parts)
        if not _apply_lookup(value, lookup, target):
            return False
    return True


def rule_matches(rule, instance):
    """
    Return True if `rule` (an ApprovalRoutingRule or AutoApprovalRule instance, or
    anything exposing .content_type_id / .content_type / .constraint) matches the
    given model instance.
    """
    model = type(instance)
    if rule.content_type_id is not None and rule.content_type.model_class() is not model:
        return False

    constraints = _normalize_constraints(rule.constraint)

    if instance.pk is not None:
        qs_filter = qs_filter_from_constraints(constraints)
        return model.objects.filter(qs_filter, pk=instance.pk).exists()

    return any(_matches_in_memory(instance, c) for c in constraints)
