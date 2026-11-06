from django.apps import AppConfig


class ShipmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shipments"

    def ready(self):
        from . import signals  # noqa: F401  (registers audit-trail signal handlers)
