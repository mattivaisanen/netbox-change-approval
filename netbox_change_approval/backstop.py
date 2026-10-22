"""
pre_save backstop: gate updates that skip full_clean().

The validator only runs when something calls full_clean(). Several NetBox
screens change objects with a plain save() instead: bulk rename, device bay
install/remove, cluster add/remove devices, virtual chassis remove member,
circuit termination swap, and "make this the primary IP" on the IP form. Custom
scripts that call save() directly do the same. Without this module those
changes needed no approval at all.

For an update that reaches save() without having been through the validator,
this runs gate() and, if approval is needed, records the request and puts the
changed fields back to their stored values before the row is written. The
save still happens, but writes nothing new. That way the screen's own
transaction keeps going (so a bulk rename queues every object, not just the
first) and nothing else in the same request has to be rolled back (an IP
created as "primary" stays as a pending create, and setting it as primary on
the device becomes its own request).

Only editable fields count. NetBox itself saves objects to update cached and
internal fields (cable ends, cable paths, cached site/location, ...); those
aren't user changes and are never gated here.

Only the outermost save is gated. A save that happens while another object
is being saved or deleted (child devices following their parent, a device's
primary IP cleared because the IP was unassigned, a virtual chassis master
update, ...) is a consequence of that outer change, which was gated itself.

Creates are not gated here: NetBox creates many objects internally with a
plain save() (components from device type templates, for example).
"""
import copy
from contextvars import ContextVar
from functools import wraps

from django.contrib import messages
from django.db.models.signals import pre_save

from netbox.context import current_request

from .context import applying_change, batch_collector
from .discovery import iter_gated_models
from .exceptions import ObjectLocked
from .models import ActionChoices

#: How many gated-model save()/delete() calls are running right now.
_write_depth = ContextVar('netbox_change_approval.write_depth', default=0)

#: Attribute the validator sets on an instance it already gated, so the save
#: that follows isn't gated a second time.
VALIDATED_ATTR = '_nbca_validated'


def _editable_values(instance):
    """The values _changed_editable_fields() compares, as they are now."""
    values = {}
    for field in instance._meta.concrete_fields:
        if not field.editable or field.primary_key or field.name.startswith('_'):
            continue
        value = getattr(instance, field.attname)
        try:
            value = copy.deepcopy(value)
        except Exception:
            pass
        values[field.attname] = value
    return values


def mark_validated(instance, request):
    """
    Called by the validator after gate() let an update through. Remembers the
    values it approved: a form may still change the object after validating
    it (several NetBox import forms set the assigned interface in save(), for
    example), and such a change must not ride along ungated.
    """
    values = _editable_values(instance)
    setattr(instance, VALIDATED_ATTR, values)
    # The REST API may validate a copy of the instance and save another, so
    # also remember it on the request.
    if request is not None:
        seen = getattr(request, '_nbca_validated', None)
        if seen is None:
            seen = {}
            try:
                request._nbca_validated = seen
            except AttributeError:
                return
        seen[(instance._meta.label_lower, instance.pk)] = values


def _consume_validated(instance, request):
    """True if the validator already let exactly this state through."""
    values = getattr(instance, VALIDATED_ATTR, None)
    if values is not None:
        delattr(instance, VALIDATED_ATTR)
    seen = getattr(request, '_nbca_validated', None) if request is not None else None
    key = (instance._meta.label_lower, instance.pk)
    if seen and key in seen:
        request_values = seen.pop(key)
        if values is None:
            values = request_values
    if values is None:
        return False
    return values == _editable_values(instance)


def _changed_editable_fields(old, new, update_fields):
    changed = []
    for field in new._meta.concrete_fields:
        if not field.editable or field.primary_key or field.name.startswith('_'):
            continue
        if update_fields is not None and field.name not in update_fields and field.attname not in update_fields:
            continue
        if getattr(old, field.attname) != getattr(new, field.attname):
            changed.append(field)
    return changed


def _revert(instance, old, fields):
    for field in fields:
        setattr(instance, field.attname, getattr(old, field.attname))
        if field.is_relation and field.is_cached(instance):
            field.delete_cached_value(instance)


def gate_plain_save(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw or instance.pk is None or applying_change.get():
        return
    if _write_depth.get() > 1:
        return  # a side effect of another object's save or delete

    request = current_request.get()
    if _consume_validated(instance, request):
        return

    try:
        old = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    changed = _changed_editable_fields(old, instance, update_fields)
    if not changed:
        return

    from .gating import gate

    try:
        change_request = gate(instance, ActionChoices.UPDATE, request)
    except ObjectLocked as exc:
        _revert(instance, old, changed)
        collector = batch_collector.get()
        if collector is not None:
            collector.locked.append(exc.change_request)
        elif request is not None:
            messages.warning(request, f'{instance}: not changed. {exc}', fail_silently=True)
        return

    if change_request is None:
        return  # bypass, auto-approved, exempt, or nothing to queue

    _revert(instance, old, changed)
    if change_request.pk is None:
        change_request.save()

    collector = batch_collector.get()
    if collector is not None:
        collector.queued.append(change_request)
    elif request is not None:
        messages.success(
            request,
            f'{old}: change submitted for approval (request #{change_request.pk}). '
            f'Approval sent to: {change_request.approver_names}.',
            fail_silently=True,
        )


def _track_depth(model, method_name):
    original = getattr(model, method_name)
    if getattr(original, '_nbca_depth_tracked', False):
        return

    @wraps(original)
    def method(self, *args, **kwargs):
        token = _write_depth.set(_write_depth.get() + 1)
        try:
            return original(self, *args, **kwargs)
        finally:
            _write_depth.reset(token)

    method._nbca_depth_tracked = True
    setattr(model, method_name, method)


def connect():
    for model in iter_gated_models():
        _track_depth(model, 'save')
        _track_depth(model, 'delete')
        pre_save.connect(
            gate_plain_save, sender=model,
            dispatch_uid=f'netbox_change_approval:{model._meta.label}:plain_save',
        )
