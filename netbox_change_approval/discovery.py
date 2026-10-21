"""
Finds every NetBox object type this plugin should gate, and wires NetBox's
CUSTOM_VALIDATORS setting so create/update requests for those types run through
our validator. Delete gating (netbox_change_approval.signals) and the detail-page
banner (netbox_change_approval.template_content) both source their model list from
here too, so there is exactly one place that decides "is this model gated".
"""
from django.apps import apps
from django.conf import settings

from netbox.models import NetBoxModel

#: dotted path of the validator every gated model gets appended under CUSTOM_VALIDATORS
VALIDATOR_PATH = 'netbox_change_approval.validators.ApprovalGateValidator'

#: this plugin's own app label — its own models are never gated (an approval rule
#: does not itself require approval; that way lies infinite regress)
OWN_APP_LABEL = 'netbox_change_approval'


def iter_gated_models():
    """
    Yield every concrete model class this plugin should gate: subclasses of NetBox's
    NetBoxModel, excluding this plugin's own models and anything listed in the
    'exempt_models' plugin setting (as 'app_label.model_name' strings).
    """
    from netbox.plugins.utils import get_plugin_config

    exempt = {
        label.lower() for label in get_plugin_config(OWN_APP_LABEL, 'exempt_models', [])
    }

    for model in apps.get_models():
        if model._meta.app_label == OWN_APP_LABEL:
            continue
        if not issubclass(model, NetBoxModel):
            continue
        if model._meta.abstract or model._meta.proxy:
            continue
        label = f'{model._meta.app_label}.{model._meta.model_name}'
        if label in exempt:
            continue
        yield model


def model_label(model):
    return f'{model._meta.app_label}.{model._meta.model_name}'


def is_gated_model(model):
    """Cheap single-model check (no apps.get_models() scan) — same rules as
    iter_gated_models(), used by middleware.py on every add-view request."""
    from netbox.plugins.utils import get_plugin_config

    if model._meta.app_label == OWN_APP_LABEL:
        return False
    if not issubclass(model, NetBoxModel):
        return False
    if model._meta.abstract or model._meta.proxy:
        return False
    exempt = {
        label.lower() for label in get_plugin_config(OWN_APP_LABEL, 'exempt_models', [])
    }
    return model_label(model) not in exempt


def wire_up_gated_models():
    """
    Merge this plugin's validator into settings.CUSTOM_VALIDATORS for every gated model.

    NetBox's dynamic config system (netbox.config.get_config()) checks
    `hasattr(settings, 'CUSTOM_VALIDATORS')` before falling back to the database-backed
    value, so setting this attribute here takes priority over anything configured via
    NetBox's own Configuration Revisions UI. We therefore *merge* into whatever is
    already on settings (e.g. from the operator's configuration.py) instead of
    replacing it, so this plugin composes with any custom validators already in use.
    """
    existing = dict(getattr(settings, 'CUSTOM_VALIDATORS', {}) or {})

    for model in iter_gated_models():
        label = model_label(model)
        validators = list(existing.get(label, []))
        if VALIDATOR_PATH not in validators:
            validators.append(VALIDATOR_PATH)
        existing[label] = validators

    settings.CUSTOM_VALIDATORS = existing
