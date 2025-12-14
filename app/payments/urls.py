"""
URL configuration for the payments app.

Routes:
    - POST /webhooks/stripe/ - Stripe webhook endpoint

All routes are prefixed with /api/v1/payments/ when included in the main URLconf.

Usage:
    # In config/urls.py
    api_v1_patterns = [
        path("payments/", include("payments.urls")),
    ]
"""

from django.urls import path

from payments.webhooks.views import stripe_webhook

app_name = "payments"

urlpatterns = [
    # Webhook endpoints
    path("webhooks/stripe/", stripe_webhook, name="stripe_webhook"),
]
