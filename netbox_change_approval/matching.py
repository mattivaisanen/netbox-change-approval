"""
Constraint-matching engine for ApprovalRule (see models.py). A rule's
`constraint` field uses the exact same shape as NetBox's own ObjectPermission.constraints: a Django ORM filter dict, or a
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
import json

from django.core.exceptions import FieldError
from django.db.models import Manager, QuerySet

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


#: Marks a path that doesn't exist on the object at all (vs. a relation that
#: is just empty/None). A condition on a missing path never matches.
_MISSING = object()


def _expand(value):
    """Turn one attribute value into the list of values to keep walking: a
    related manager or list (e.g. a cable's a_terminations) fans out to its
    items, anything else stays a single value."""
    if isinstance(value, Manager):
        try:
            return list(value.all())
        except ValueError:  # related manager on an unsaved instance
            return []
    if isinstance(value, (list, tuple, set, QuerySet)):
        return list(value)
    return [value]


def _pending_m2m(instance, name):
    """
    The many-to-many values (tags, ...) a form or the API is about to save on
    `instance`, or None if this save doesn't set `name`. NetBox puts them in
    `instance._m2m_values` before validation; the database still has the old
    ones. Without this, {"tags__slug": "lab"} would see the old tags, so an
    edit that removes the "lab" tag would still count as a lab object.
    """
    pending = getattr(instance, '_m2m_values', None)
    if not pending or name not in pending:
        return None
    values = list(pending[name])
    pks = [v for v in values if not hasattr(v, '_meta')]
    if pks:
        try:
            related_model = instance._meta.get_field(name).related_model
        except Exception:
            return [v for v in values if hasattr(v, '_meta')]
        found = {o.pk: o for o in related_model.objects.filter(pk__in=pks)}
        values = [v if hasattr(v, '_meta') else found.get(v) for v in values]
    return [v for v in values if v is not None]


def _resolve_values(obj, parts):
    """
    All values reachable from `obj` along `parts`. Follows foreign keys like
    getattr() does, and fans out over many-valued attributes, so e.g.
    a_terminations__device__site__region__slug on a cable yields the region
    slug of every A-side termination's device.
    """
    values = [obj]
    for depth, part in enumerate(parts):
        next_values = []
        for value in values:
            if value is None or value is _MISSING:
                next_values.append(value)
                continue
            if depth == 0 and (pending := _pending_m2m(value, part)) is not None:
                next_values.extend(pending)
                continue
            try:
                attr = getattr(value, part)
            except AttributeError:
                next_values.append(_MISSING)
                continue
            except Exception:  # e.g. RelatedObjectDoesNotExist
                next_values.append(None)
                continue
            next_values.extend(_expand(attr))
        values = next_values
    return values or [None]


def _comparable(value, target):
    """Let {"site": 5} match a Site instance with pk 5."""
    if hasattr(value, '_meta') and hasattr(value, 'pk') and not hasattr(target, '_meta'):
        return value.pk
    return value


def _apply_lookup(value, lookup, target):
    if lookup == 'in':
        return any(_comparable(value, t) == t for t in target)
    value = _comparable(value, target)
    if lookup == 'exact':
        return value == target
    if lookup == 'iexact':
        return str(value).lower() == str(target).lower()
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
        values = [v for v in _resolve_values(instance, parts) if v is not _MISSING]
        if not any(_apply_lookup(v, lookup, target) for v in values):
            return False
    return True


def _matches_new_state(instance, constraints):
    """In-memory check of the values about to be saved. Anything we can't
    evaluate in memory counts as "no match" — the safe direction, since it
    means the change goes to an approver instead of being waved through."""
    try:
        return any(_matches_in_memory(instance, c) for c in constraints)
    except (ValueError, TypeError):
        return False


def _matches_saved_state(model, instance, constraints):
    try:
        qs_filter = qs_filter_from_constraints(constraints)
        return model.objects.filter(qs_filter, pk=instance.pk).exists()
    except (FieldError, ValueError, TypeError):
        pass
    # The database can't evaluate this path: either it goes through a Python
    # property rather than a real field (e.g. Cable.a_terminations), or the
    # model simply doesn't have it ({"status": ...} on a Manufacturer). Check
    # the stored row in memory instead; a path that doesn't exist never matches.
    try:
        stored = model.objects.get(pk=instance.pk)
    except model.DoesNotExist:
        return False
    return _matches_new_state(stored, constraints)


def rule_matches(rule, instance, check_new_state=False):
    """
    Return True if `rule` (an ApprovalRule, or
    anything exposing .content_type_id / .content_type / .constraint) matches the
    given model instance.

    For a saved instance this checks the row as stored in the database. With
    `check_new_state=True` (auto-approval of an UPDATE) the in-memory values
    about to be saved must match too: a rule like {"status": "planned"} then
    auto-approves edits to a planned object that leave it planned, but not the
    edit that changes it to active.
    """
    model = type(instance)
    if rule.content_type_id is not None and rule.content_type.model_class() is not model:
        return False

    constraints = _normalize_constraints(rule.constraint)

    if instance.pk is None:
        return _matches_new_state(instance, constraints)

    if not _matches_saved_state(model, instance, constraints):
        return False
    if check_new_state:
        return _matches_new_state(instance, constraints)
    return True


def _path_error(model, constraint):
    """Why `constraint` (one filter dict) can't work on `model`, or None."""
    try:
        str(model.objects.filter(qs_filter_from_constraints([constraint])).query)
        return None
    except FieldError as error:
        # Not a database field. Paths through a Python property (a cable's
        # a_terminations, say) are matched in memory, so accept those.
        if all(hasattr(model, _split_lookup(path)[0][0]) for path in constraint):
            return None
        return str(error)
    except (ValueError, TypeError) as error:  # {"site": "abc"}: wrong kind of value
        return str(error)
    except Exception as error:  # noqa: BLE001  anything else the ORM rejects
        return str(error)


def constraint_errors(constraint, models):
    """
    Problems with a rule's constraint, as messages for the rule form; empty
    if it's fine. `models` are the object types the rule can apply to: the
    constraint must work on at least one of them (a rule for any object type
    may well name a field only some types have).
    """
    if not constraint:
        return []
    filters = constraint if isinstance(constraint, list) else [constraint]
    if not all(isinstance(f, dict) and all(isinstance(k, str) for k in f) for f in filters):
        return ['Must be an object such as {"status": "planned"}, or a list of them.']
    messages = []
    for constraint_dict in filters:
        if not constraint_dict:
            continue
        errors = [_path_error(model, constraint_dict) for model in models]
        if errors and all(errors):
            messages.append(errors[0] if len(models) == 1 else
                            f'{json.dumps(constraint_dict, default=str)} fits none of the object types: {errors[0]}')
    return messages
