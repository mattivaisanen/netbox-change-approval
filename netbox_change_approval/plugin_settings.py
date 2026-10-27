"""
Plugin settings superusers can change in NetBox (Change Approval → Settings).

configuration.py (PLUGINS_CONFIG['netbox_change_approval']) gives every
setting its value, as before. A value saved on the settings page overrides it
until someone presses "Use configuration.py" next to it. Saving a value equal
to the configuration.py value removes the override, so the two stay in sync:
the page only stores what differs.

Saved values are PluginSetting rows, read when a setting is used (at most once
per web request), never cached at startup. A change therefore applies at once
in every NetBox process and in the background worker, without a restart.

`exempt_models` is not editable here: it is the emergency off switch, read
when NetBox starts to decide which models are wired up at all. It must work
even when this plugin, its database table or its pages are what's broken, and
a value saved in the database must never be able to override it. The page
shows it read-only.

Changing a setting doesn't need approval: PluginSetting is one of this
plugin's own models, which are never gated (discovery.py).
"""
from dataclasses import dataclass, field

from django.db import connection
from django.utils.translation import gettext_lazy as _

APP = 'netbox_change_approval'

#: Attribute on the current request that holds this request's saved values.
_MEMO_ATTR = '_change_approval_settings'

KIND_GROUP = 'group'
KIND_DAYS = 'days'
KIND_CHOICE = 'choice'
KIND_BOOL = 'bool'
KIND_TEMPLATE = 'template'


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    help_text: str
    kind: str
    choices: tuple = field(default=())

    def normalize(self, value):
        """The value as this plugin uses it, so equal meanings compare equal
        (e.g. 0 and None both mean "never expire")."""
        if self.kind == KIND_BOOL:
            return value is not False
        if self.kind == KIND_DAYS:
            try:
                return int(value) if value else None
            except (TypeError, ValueError):
                return None
        if self.kind == KIND_CHOICE:
            keys = [key for key, _label in self.choices]
            return value if value in keys else keys[0]
        if self.kind == KIND_TEMPLATE:
            from .message_template import DEFAULT, clean
            return clean(value).strip() or DEFAULT
        return value or None


SETTINGS = (
    Setting(
        'pending_row_style', _('Pending row style'),
        _('How pending objects look in object lists and rack elevations.'),
        KIND_CHOICE,
        choices=(
            ('by_action', _('By action: green new, blue edit, red delete, with the requested values')),
            ('outline', _('Outline: one yellow outline for every pending object')),
        ),
    ),
    Setting(
        'group_components_with_parent', _('Group components with their new device or VM'),
        _('Interfaces, ports, IPs and other components of a device or VM waiting for approval '
          'belong to its request instead of getting their own.'),
        KIND_BOOL,
    ),
    Setting(
        'work_sheets', _('Field work sheet'),
        _('Turn approved changes that need work on site into tasks on the Work Sheet.'),
        KIND_BOOL,
    ),
    Setting(
        'change_sets', _('Change sets'),
        _('Let people collect several changes in a change set and submit them for approval as one.'),
        KIND_BOOL,
    ),
    Setting(
        'webhook_message', _('Webhook message'),
        _('The text webhooks get as {{ data.message }}, in Jinja2. Variables: by, by_email, title, action, '
          'object_type, object, comment, link, id, status, reason, submitted_by, submitted_by_email, '
          'approver_group. Teams needs a blank line between lines. Empty: the default text.'),
        KIND_TEMPLATE,
    ),
)

BY_KEY = {setting.key: setting for setting in SETTINGS}

#: Only configuration.py sets these (see module docstring).
CONFIG_ONLY = ('exempt_models',)

#: Whether the PluginSetting table exists in this process's database yet. Code
#: can run before `migrate` (e.g. during the migration that creates it).
_table_ready = False


def config_value(key, default=None):
    """The configuration.py value (or the plugin default)."""
    from netbox.plugins.utils import get_plugin_config

    return get_plugin_config(APP, key, default)


def plugin_default(key):
    from . import config

    return config.default_settings.get(key)


def _load():
    global _table_ready
    from .models import PluginSetting

    if not _table_ready:
        if PluginSetting._meta.db_table not in connection.introspection.table_names():
            return {}
        _table_ready = True
    return {
        key: value
        for key, value in PluginSetting.objects.filter(key__in=BY_KEY).values_list('key', 'value')
    }


def saved_values():
    """{key: saved value} for every setting saved on the settings page.
    Read from the database once per request (every use outside a request)."""
    from netbox.context import current_request

    request = current_request.get()
    memo = getattr(request, _MEMO_ATTR, None) if request is not None else None
    if memo is not None:
        return memo
    values = _load()
    if request is not None:
        try:
            setattr(request, _MEMO_ATTR, values)
        except AttributeError:
            pass
    return values


def forget_request_values():
    from netbox.context import current_request

    request = current_request.get()
    if request is not None and hasattr(request, _MEMO_ATTR):
        delattr(request, _MEMO_ATTR)


def get_setting(key, default=None):
    """The value in effect: saved on the settings page, else configuration.py,
    else the plugin default. Use this instead of get_plugin_config()."""
    if key in BY_KEY:
        saved = saved_values()
        if key in saved:
            return saved[key]
    return config_value(key, default)


def source(key):
    """Where the value in effect comes from: 'saved', 'config' or 'default'."""
    if key in BY_KEY and key in saved_values():
        return 'saved'
    if key in BY_KEY:
        setting = BY_KEY[key]
        if setting.normalize(config_value(key)) == setting.normalize(plugin_default(key)):
            return 'default'
    elif config_value(key) == plugin_default(key):
        return 'default'
    return 'config'


def save(key, value, user=None):
    """Store `value` for `key`, or drop the saved value when it means the same
    as configuration.py's. Returns True if a value is now saved."""
    from .models import PluginSetting

    setting = BY_KEY[key]
    if setting.normalize(value) == setting.normalize(config_value(key)):
        reset(key)
        return False
    previous = saved_values().get(key, object())
    PluginSetting.objects.update_or_create(key=key, defaults={'value': value, 'updated_by': user})
    forget_request_values()
    if previous != value:
        _after_change(key)
    return True


def reset(key):
    """Go back to the configuration.py value."""
    from .models import PluginSetting

    deleted, _rows = PluginSetting.objects.filter(key=key).delete()
    forget_request_values()
    if deleted:
        _after_change(key)
    return bool(deleted)


def _after_change(key):
    """Hook for settings that need more than a fresh read when they change."""
