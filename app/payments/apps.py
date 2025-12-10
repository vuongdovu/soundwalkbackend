"""
Django app configuration for payments.
"""

from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    """Payments app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "payments"
    verbose_name = "Payments & Subscriptions"

    def ready(self):
        """Import signals when app is ready."""
        # TODO: Uncomment when signals are implemented
        # from . import signals  # noqa: F401
        pass
