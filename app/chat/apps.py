"""
Django app configuration for chat.
"""

from django.apps import AppConfig


class ChatConfig(AppConfig):
    """Chat app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"
    verbose_name = "Chat & Messaging"

    def ready(self):
        """Import signals when app is ready."""
        # TODO: Uncomment when signals are implemented
        # from . import signals  # noqa: F401
        pass
