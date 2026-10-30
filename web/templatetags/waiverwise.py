from django import template
from django.utils import timezone

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


@register.filter
def short_age(value):
    """Compact age for the app bar: 12m, 3h, 2d."""
    if value is None:
        return ""
    minutes = int((timezone.now() - value).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 0)}m"
    return f"{minutes // 60}h" if minutes < 60 * 24 else f"{minutes // (60 * 24)}d"


SLOT_LABELS = {"RB/WR/TE": "FLEX"}
STATUS_LABELS = {"INJURY_RESERVE": "IR"}


@register.filter
def slot_label(slot):
    return SLOT_LABELS.get(slot, slot)


@register.filter
def status_label(status):
    """ESPN's QUESTIONABLE / INJURY_RESERVE as people say them."""
    return STATUS_LABELS.get(status, status.replace("_", " ").capitalize())
