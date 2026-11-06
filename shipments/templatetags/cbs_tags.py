from django import template
from django.utils.html import format_html

from shipments.models import Notification, Status

register = template.Library()

_STATUS_LABELS = dict(Status.choices)
_KIND_ICONS = {
    "STALE": "bi-clock-history", "OVERDUE": "bi-alarm", "AWAITING_POD": "bi-patch-question",
    "NO_WAYBILL": "bi-file-earmark-x", "DUPLICATE": "bi-files", "DELIVERED": "bi-check-circle",
    "FAILED": "bi-exclamation-octagon", "ASSIGNED": "bi-person-check", "SYSTEM": "bi-info-circle",
}


@register.simple_tag
def status_badge(status):
    return format_html(
        '<span class="badge status status-{}">{}</span>', str(status).lower(), _STATUS_LABELS.get(status, status)
    )


@register.filter
def kind_icon(kind):
    return _KIND_ICONS.get(kind, "bi-bell")


@register.filter
def mask_name(name):
    """'Jane Mwangi' -> 'J*** M*****' (public tracking page privacy)."""
    parts = str(name or "").split()
    return " ".join(p[0] + "*" * (len(p) - 1) for p in parts)


@register.filter
def status_label(value):
    return _STATUS_LABELS.get(value, value)


@register.filter
def is_pair(value):
    """True for an [old, new] change pair stored in the audit log."""
    return isinstance(value, (list, tuple)) and len(value) == 2


@register.filter
def field_label(key):
    from shipments.services import FIELD_LABELS

    return FIELD_LABELS.get(key, str(key).replace("_", " ").capitalize())


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def display_name(user, fallback="-"):
    """Full name, else username, else a fallback (safe for None / unassigned)."""
    if not user:
        return fallback
    return user.get_full_name() or user.get_username()
