from django.conf import settings

from accounts.models import get_profile


def cbsrms(request):
    """Variables available in every template."""
    ctx = {
        "CBSRMS": settings.CBSRMS,
        "company_name": settings.CBSRMS["COMPANY_NAME"],
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        ctx["profile"] = get_profile(user)
        ctx["unread_count"] = user.notifications.filter(is_read=False).count()
    return ctx
