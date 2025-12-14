"""
Webhook handling for payment events from Stripe.

This module provides views and handlers for processing Stripe webhooks.
Webhooks are verified, stored idempotently, and processed asynchronously
via Celery tasks.

Usage:
    # In urls.py
    from payments.webhooks.views import stripe_webhook

    urlpatterns = [
        path("webhooks/stripe/", stripe_webhook, name="stripe_webhook"),
    ]
"""

from payments.webhooks.handlers import dispatch_webhook, register_handler
from payments.webhooks.views import stripe_webhook

__all__ = [
    "dispatch_webhook",
    "register_handler",
    "stripe_webhook",
]
