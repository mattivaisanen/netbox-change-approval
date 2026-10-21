"""
Generic snapshot/diff/apply helpers that work against any Django model's concrete
fields and many-to-many relations, without per-model code. Deliberately field-level
rather than DRF-serializer-based: by the time our validator sees an instance (inside
NetBoxModel.clean(), called from a ModelForm's _post_clean()), Django has already
assigned every new scalar/FK value onto the in-memory instance and stashed pending
M2M values on `instance._m2m_values` (NetBox's own NetBoxModelForm does this
specifically so custom validators can see them — see CustomValidator._get_instance_attr
in NetBox core). Working at this level means apply() is just "set these attributes
and save", which is the same thing every NetBox ModelForm already does.

Known limitation (documented in README): tags (django-taggit) are not a plain
ManyToManyField and are not currently captured in the diff — a *user's own* tag
changes on a gated object apply immediately, uncaptured by approval. (This is
unrelated to the plugin's own "Pending Approval" tag, applied directly via
tags.py/signals.py outside of this diff machinery entirely.) Everything else
that is a concrete field or a real ManyToManyField (including
custom_field_data, which is a plain JSONField) is covered.
"""
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import FieldDoesNotExist

#: Types ChangeRequest.diff's DjangoJSONEncoder already knows how to serialize
#: (it special-cases these in its .default()) — pass them through as-is so
#: apply-time _coerce()/field.to_python() still gets back a real Decimal/date/
#: datetime/UUID, not a string. Anything NOT one of these, and not already a
#: bare JSON primitive, gets stringified below (see _json_safe) — e.g. an
#: ImageField/FileField attribute is a FieldFile object, not a plain value,
#: and json.dumps() has no idea what to do with one.
_JSON_NATIVE_TYPES = (type(None), str, int, float, bool, dict, list)
_ENCODER_HANDLED_TYPES = (Decimal, date, datetime, time, UUID)


def _json_safe(value):
    if isinstance(value, _JSON_NATIVE_TYPES) or isinstance(value, _ENCODER_HANDLED_TYPES):
        return value
    return str(value)


def _concrete_fields(model):
    pk_attname = model._meta.pk.attname
    return [
        f for f in model._meta.get_fields()
        if getattr(f, 'concrete', False) and not f.many_to_many and f.attname != pk_attname
        and not getattr(f, 'auto_now', False) and not getattr(f, 'auto_now_add', False)
    ]


def _m2m_fields(model):
    return list(model._meta.local_many_to_many)


def _field_map(model):
    mapping = {f.attname: f for f in _concrete_fields(model)}
    mapping.update({f.name: f for f in _m2m_fields(model)})
    return mapping


def _new_m2m_value(instance, field):
    """Return the list of PKs this instance *should* end up with for `field`, or
    None if we have no way to know yet (field untouched by this save)."""
    m2m_values = getattr(instance, '_m2m_values', None)
    if m2m_values and field.name in m2m_values:
        return [obj.pk for obj in m2m_values[field.name]]
    if instance.pk:
        return list(getattr(instance, field.name).values_list('pk', flat=True))
    return None


def snapshot_create(instance):
    """
    Field dump for a not-yet-saved instance — the audit record of what was
    requested, stored on ChangeRequest.diff. (Create requests don't need a
    reconstruction blueprint: the object is written immediately and tagged
    "Pending Approval" rather than queued — see gating.gate()'s docstring —
    so there's nothing to "apply" later beyond removing that tag.) Fields left
    at their model default (field.get_default(), Django's own "nothing was set"
    value — '' or None, matching each field's own empty-value convention) are
    skipped: they're just noise in the approver-facing diff.
    """
    model = type(instance)
    data = {}
    for field in _concrete_fields(model):
        value = getattr(instance, field.attname)
        if value == field.get_default():
            continue
        data[field.attname] = _json_safe(value)
    for field in _m2m_fields(model):
        value = _new_m2m_value(instance, field)
        if value:
            data[field.name] = value
    return data


def snapshot_update(old_instance, new_instance):
    """{field: {"old": ..., "new": ...}} for fields that actually changed."""
    model = type(new_instance)
    diff = {}
    for field in _concrete_fields(model):
        old_value = getattr(old_instance, field.attname)
        new_value = getattr(new_instance, field.attname)
        if old_value != new_value:
            diff[field.attname] = {'old': _json_safe(old_value), 'new': _json_safe(new_value)}
    for field in _m2m_fields(model):
        new_value = _new_m2m_value(new_instance, field)
        if new_value is None:
            continue
        old_value = list(getattr(old_instance, field.name).values_list('pk', flat=True))
        if sorted(old_value) != sorted(new_value):
            diff[field.name] = {'old': old_value, 'new': new_value}
    return diff


def _coerce(field, value):
    if value is None:
        return None
    return field.to_python(value)


def apply_update(content_type, object_id, diff):
    model = content_type.model_class()
    instance = model.objects.get(pk=object_id)
    field_map = _field_map(model)
    m2m_updates = {}

    for key, change in diff.items():
        try:
            field = field_map[key]
        except KeyError:
            raise FieldDoesNotExist(
                f"'{key}' is no longer a field on {model._meta.label} — cannot apply this change."
            )
        if field.many_to_many:
            m2m_updates[field.name] = change['new']
        else:
            setattr(instance, field.attname, _coerce(field, change['new']))

    instance.full_clean()
    instance.save()
    for name, ids in m2m_updates.items():
        getattr(instance, name).set(ids)
    return instance


def apply_delete(content_type, object_id):
    model = content_type.model_class()
    instance = model.objects.get(pk=object_id)
    instance.delete()
