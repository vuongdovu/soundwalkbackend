"""
Django app configuration for utils.
"""

from django.apps import AppConfig


class UtilsConfig(AppConfig):
    """Configuration for the utils application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "utils"
    verbose_name = "Utilities"

    def ready(self):
        """
        Perform app initialization.

        This app has no signals to import, but the method is here
        for consistency and potential future use.
        """
        pass
