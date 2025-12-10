"""
Django app configuration for AI.
"""

from django.apps import AppConfig


class AIConfig(AppConfig):
    """AI app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ai"
    verbose_name = "AI Integration"

    def ready(self):
        """Import signals when app is ready."""
        # TODO: Uncomment when signals are implemented
        # from . import signals  # noqa: F401
        pass
