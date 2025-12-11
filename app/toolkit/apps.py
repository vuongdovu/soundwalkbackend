"""
Django app configuration for toolkit.
"""

from django.apps import AppConfig


class ToolkitConfig(AppConfig):
    """Configuration for the toolkit application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "toolkit"
    verbose_name = "Toolkit"

    def ready(self):
        """
        Perform app initialization.

        This app has no signals to import, but the method is here
        for consistency and potential future use.
        """
        pass
