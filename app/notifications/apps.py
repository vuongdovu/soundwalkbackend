"""Django app configuration for notifications."""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Configuration for the notifications app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"
    verbose_name = "Notifications"
