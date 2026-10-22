from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def task_text(task):
    """A work task's text with the objects it names linked (WorkTask.links)."""
    text = task.text or ''
    parts, end = [], 0
    for item in task.links or []:
        try:
            label, url = item
        except (TypeError, ValueError):
            continue
        start = text.find(label, end)
        if start == -1 or not str(url).startswith('/'):
            continue
        parts.append(escape(text[end:start]))
        parts.append(f'<a href="{escape(url)}">{escape(label)}</a>')
        end = start + len(label)
    parts.append(escape(text[end:]))
    return mark_safe(''.join(parts))
