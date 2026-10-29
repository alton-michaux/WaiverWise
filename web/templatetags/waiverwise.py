from django import template

register = template.Library()


@register.filter
def pct(value):
    return "" if value is None else f"{value * 100:.0f}%"


@register.filter
def pts(value):
    return "" if value is None else f"{value:.1f}"


@register.filter
def signed(value):
    return "" if value is None else f"{value:+.1f}"
