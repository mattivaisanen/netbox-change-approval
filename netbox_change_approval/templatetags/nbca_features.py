from django import template

from .. import features

register = template.Library()


@register.simple_tag
def feature_on(key):
    """{% feature_on 'comments' as comments_on %}: whether an optional
    feature (features.py) is turned on."""
    return features.on(key)
