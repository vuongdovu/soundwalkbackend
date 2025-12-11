"""
Django app configuration for authentication.
"""

from django.apps import AppConfig


class AuthenticationConfig(AppConfig):
    """Configuration for the authentication application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "authentication"
    verbose_name = "Authentication"

    def ready(self):
        """
        Import signals when the app is ready.

        This ensures signal handlers are connected when Django starts.
        """
        from authentication import signals  # noqa: F401
