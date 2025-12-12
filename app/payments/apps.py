"""
Payments app configuration.

This app provides payment processing infrastructure including:
- Double-entry bookkeeping ledger
- (Future) Stripe integration
- (Future) Webhook handling
"""

from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    """Configuration for the payments application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "payments"
    verbose_name = "Payments"
