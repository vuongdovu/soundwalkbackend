"""
Django app configuration for notifications.
"""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Notifications app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"
    verbose_name = "Notifications"

    def ready(self):
        """Import signal handlers when app is ready."""
        # TODO: Uncomment when handlers are implemented
        # from . import handlers  # noqa: F401
        pass
