"""
Finds every NetBox object type this plugin should gate, and runs our validator
on create/update of those types (from NetBox's post_clean signal, next to
CUSTOM_VALIDATORS). Delete gating (netbox_change_approval.signals) and the detail-page
banner (netbox_change_approval.template_content) both source their model list from
here too, so there is exactly one place that decides "is this model gated".
"""
from django.apps import apps

from netbox.models import NetBoxModel

#: dotted path of the validator every gated model runs on create/update
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
    Run this plugin's validator on create/update of every gated model.

    NetBox runs CUSTOM_VALIDATORS from a post_clean receiver
    (extras.signals.run_save_validators), reading them through get_config():
    from configuration.py if set there, else from the active Configuration
    Revision. This connects a second post_clean receiver per gated model
    instead of adding to settings.CUSTOM_VALIDATORS: a value written to
    settings would hide the Configuration Revision value and lock that field
    in NetBox's form. NetBox's receiver is connected first, so the operator's
    own validators still run before ours, as when ours was appended to them.
    """
    from netbox.signals import post_clean

    for model in iter_gated_models():
        post_clean.connect(
            run_approval_validator, sender=model,
            dispatch_uid=f'netbox_change_approval:{model._meta.label}:validate',
        )


def run_approval_validator(sender, instance, **kwargs):
    # NetBox's own runner: same request lookup and validator call as for
    # CUSTOM_VALIDATORS.
    from extras.utils import run_validators

    run_validators(instance, [VALIDATOR_PATH])
