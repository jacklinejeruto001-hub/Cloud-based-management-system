"""
Stores the current user and client IP in thread-local storage so that model
signal handlers (which have no access to `request`) can write accurate
audit-trail entries.
"""
import threading

_state = threading.local()


def get_current_user():
    user = getattr(_state, "user", None)
    return user if user is not None and user.is_authenticated else None


def get_current_ip():
    return getattr(_state, "ip", None)


def set_current(user=None, ip=None):
    _state.user = user
    _state.ip = ip


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current(getattr(request, "user", None), client_ip(request))
        try:
            return self.get_response(request)
        finally:
            set_current(None, None)
