"""
The settings page (Change Approval → Settings), superusers only. See
plugin_settings.py for how saved values and configuration.py fit together.
"""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from utilities.views import ConditionalLoginRequiredMixin

from . import plugin_settings
from .plugin_settings import KIND_BOOL, KIND_CHOICE, KIND_DAYS, KIND_GROUP, KIND_TEMPLATE


def _field(setting):
    if setting.kind == KIND_GROUP:
        from users.models import Group

        return forms.ModelChoiceField(
            queryset=Group.objects.order_by('name'), to_field_name='name', required=False,
            label=setting.label, help_text=setting.help_text,
        )
    if setting.kind == KIND_DAYS:
        return forms.IntegerField(
            min_value=0, required=False, label=setting.label, help_text=setting.help_text,
        )
    if setting.kind == KIND_CHOICE:
        return forms.ChoiceField(choices=setting.choices, label=setting.label, help_text=setting.help_text)
    if setting.kind == KIND_BOOL:
        return forms.BooleanField(required=False, label=setting.label, help_text=setting.help_text)
    if setting.kind == KIND_TEMPLATE:
        return forms.CharField(
            required=False, strip=False, label=setting.label, help_text=setting.help_text,
            widget=forms.Textarea(attrs={'rows': 10, 'class': 'font-monospace'}),
            validators=[_valid_template],
        )
    raise ValueError(setting.kind)


def _valid_template(value):
    from .message_template import check

    try:
        check(value)
    except ValueError as e:
        raise forms.ValidationError(_('This template has an error: %(error)s') % {'error': e})


def _stored(setting, cleaned):
    """The form value as it is stored (JSON) and used: a group by its name."""
    if setting.kind == KIND_GROUP:
        return cleaned.name if cleaned is not None else None
    if setting.kind == KIND_DAYS:
        return cleaned or None
    if setting.kind == KIND_TEMPLATE:
        return setting.normalize(cleaned)
    return cleaned


class PluginSettingsForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for setting in plugin_settings.SETTINGS:
            self.fields[setting.key] = _field(setting)
            if not self.is_bound:
                self.initial[setting.key] = setting.normalize(plugin_settings.get_setting(setting.key))

    def stored_values(self):
        return {
            setting.key: _stored(setting, self.cleaned_data.get(setting.key))
            for setting in plugin_settings.SETTINGS
        }


def _display(setting, value):
    value = setting.normalize(value)
    if setting.kind == KIND_BOOL:
        return _('On') if value else _('Off')
    if setting.kind == KIND_DAYS:
        return str(value) if value else _('Never')
    if setting.kind == KIND_CHOICE:
        return str(dict(setting.choices).get(value, value))
    if setting.kind == KIND_TEMPLATE:
        from .message_template import DEFAULT
        return _('the default text') if value == DEFAULT else _('its own text')
    return value or _('None')


class PluginSettingsView(ConditionalLoginRequiredMixin, View):
    template_name = 'netbox_change_approval/settings.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not request.user.is_superuser:
            raise PermissionDenied(_('Only superusers can change Change Approval settings.'))
        return super().dispatch(request, *args, **kwargs)

    def _render(self, request, form):
        from users.models import Group

        from .models import PluginSetting

        saved = {row.key: row for row in PluginSetting.objects.select_related('updated_by')}
        rows = []
        for setting in plugin_settings.SETTINGS:
            value = plugin_settings.get_setting(setting.key)
            missing_group = (
                setting.kind == KIND_GROUP and value and not Group.objects.filter(name=value).exists()
            )
            rows.append({
                'field': form[setting.key],
                'source': plugin_settings.source(setting.key),
                'saved': saved.get(setting.key),
                'config_display': _display(setting, plugin_settings.config_value(setting.key)),
                'missing_group': value if missing_group else None,
            })
        return render(request, self.template_name, {
            'form': form,
            'rows': rows,
            'exempt_models': plugin_settings.config_value('exempt_models', []) or [],
        })

    def get(self, request):
        return self._render(request, PluginSettingsForm())

    def post(self, request):
        url = reverse('plugins:netbox_change_approval:settings')
        reset_key = request.POST.get('reset')
        if reset_key:
            if reset_key in plugin_settings.BY_KEY and plugin_settings.reset(reset_key):
                messages.success(request, _('%(setting)s now follows configuration.py.') % {
                    'setting': plugin_settings.BY_KEY[reset_key].label,
                })
            return redirect(url)

        form = PluginSettingsForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)
        before = {key: plugin_settings.get_setting(key) for key in plugin_settings.BY_KEY}
        changed = []
        for key, value in form.stored_values().items():
            setting = plugin_settings.BY_KEY[key]
            plugin_settings.save(key, value, user=request.user)
            if setting.normalize(before[key]) != setting.normalize(value):
                changed.append(str(setting.label))
        if changed:
            messages.success(request, _('Saved: %(settings)s. In effect now.') % {'settings': ', '.join(changed)})
        else:
            messages.info(request, _('Nothing changed.'))
        return redirect(url)
