from django import template

register = template.Library()


@register.filter
def has_old_values(rows):
    """False when no row has an old value (a new object): the quick view then
    leaves out its Old column instead of a column of dashes."""
    return any(row.get('old') is not None for row in rows or ())


@register.filter
def is_blank(value):
    """None or an empty string: shown the same way, as a gray dash."""
    return value is None or value == ''


@register.filter
def short_age(value):
    """How long ago, in its largest unit only ("10 hours ago", not "10 hours,
    47 minutes ago"), so it fits a narrow column. The full time is in the title."""
    from django.utils.timesince import timesince
    from django.utils.translation import gettext

    if not value:
        return ''
    return gettext('%(time)s ago') % {'time': timesince(value, depth=1)}
