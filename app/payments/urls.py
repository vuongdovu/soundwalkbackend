"""
URL configuration for payments app.

Routes:
    GET /subscription/ - Get current subscription
    POST /checkout/ - Create checkout session
    POST /billing-portal/ - Create billing portal session
    POST /cancel/ - Cancel subscription
    GET /transactions/ - List transactions
    GET /invoices/ - List invoices
    POST /webhook/ - Stripe webhook endpoint

Usage in config/urls.py:
    path("api/v1/payments/", include("payments.urls")),
"""

from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    # Subscription management
    path(
        "subscription/",
        views.SubscriptionView.as_view(),
        name="subscription",
    ),
    path(
        "checkout/",
        views.CreateCheckoutSessionView.as_view(),
        name="checkout",
    ),
    path(
        "billing-portal/",
        views.CreateBillingPortalView.as_view(),
        name="billing-portal",
    ),
    path(
        "cancel/",
        views.CancelSubscriptionView.as_view(),
        name="cancel",
    ),
    # History
    path(
        "transactions/",
        views.TransactionListView.as_view(),
        name="transactions",
    ),
    path(
        "invoices/",
        views.InvoiceListView.as_view(),
        name="invoices",
    ),
    # Webhook
    path(
        "webhook/",
        views.StripeWebhookView.as_view(),
        name="webhook",
    ),
]
