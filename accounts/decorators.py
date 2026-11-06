from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import get_profile


def permission_required(attr):
    """
    Restrict a view to users whose Profile has a truthy permission property,
    e.g. @permission_required("can_create_shipments").
    Anonymous users are redirected to the login page; logged-in users without
    the permission receive a 403 page.
    """

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not getattr(get_profile(request.user), attr, False):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
