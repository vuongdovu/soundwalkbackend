"""
DRF serializers for payments app.

This module provides serializers for:
- Subscription display and updates
- Transaction history
- Checkout session requests

Related files:
    - models.py: Subscription, Transaction
    - views.py: Payment API views

Usage:
    serializer = SubscriptionSerializer(subscription)
    data = serializer.data
"""

from __future__ import annotations

from rest_framework import serializers


class SubscriptionSerializer(serializers.Serializer):
    """
    Subscription serializer for API responses.

    Fields:
        id: Subscription ID
        plan_name: Human-readable plan name
        status: Current subscription status
        current_period_start: Period start date
        current_period_end: Period end date
        cancel_at_period_end: Whether canceling at period end
        is_active: Computed active status
        is_trialing: Whether in trial period
        features: Plan features from metadata

    Usage:
        serializer = SubscriptionSerializer(user.subscription)
    """

    # TODO: Implement serializer fields
    # id = serializers.IntegerField(read_only=True)
    # plan_name = serializers.CharField(read_only=True)
    # status = serializers.CharField(read_only=True)
    # current_period_start = serializers.DateTimeField(read_only=True)
    # current_period_end = serializers.DateTimeField(read_only=True)
    # cancel_at_period_end = serializers.BooleanField(read_only=True)
    # is_active = serializers.BooleanField(read_only=True)
    # is_trialing = serializers.BooleanField(read_only=True)
    # features = serializers.SerializerMethodField()
    #
    # def get_features(self, obj) -> dict:
    #     """Extract features from metadata."""
    #     return obj.metadata.get("features", {})
    pass


class TransactionSerializer(serializers.Serializer):
    """
    Transaction serializer for payment history.

    Fields:
        id: Transaction ID
        transaction_type: Type of transaction
        status: Transaction status
        amount_display: Formatted amount (e.g., "$10.00 USD")
        description: Transaction description
        created_at: Transaction date

    Usage:
        transactions = user.transactions.all()[:10]
        serializer = TransactionSerializer(transactions, many=True)
    """

    # TODO: Implement serializer fields
    # id = serializers.IntegerField(read_only=True)
    # transaction_type = serializers.CharField(read_only=True)
    # status = serializers.CharField(read_only=True)
    # amount_display = serializers.CharField(read_only=True)
    # description = serializers.CharField(read_only=True)
    # created_at = serializers.DateTimeField(read_only=True)
    pass


class CreateCheckoutSessionSerializer(serializers.Serializer):
    """
    Serializer for checkout session creation.

    Fields:
        price_id: Stripe Price ID to subscribe to
        success_url: URL to redirect on success
        cancel_url: URL to redirect on cancel

    Usage:
        serializer = CreateCheckoutSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session_url = StripeService.create_checkout_session(
            user=request.user,
            **serializer.validated_data
        )
    """

    price_id = serializers.CharField(
        max_length=255,
        help_text="Stripe Price ID (price_xxx)",
    )
    success_url = serializers.URLField(
        help_text="URL to redirect after successful checkout",
    )
    cancel_url = serializers.URLField(
        help_text="URL to redirect if checkout is canceled",
    )


class CreateBillingPortalSerializer(serializers.Serializer):
    """
    Serializer for billing portal session creation.

    Fields:
        return_url: URL to return to after portal session

    Usage:
        serializer = CreateBillingPortalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        portal_url = StripeService.create_billing_portal_session(
            user=request.user,
            return_url=serializer.validated_data["return_url"]
        )
    """

    return_url = serializers.URLField(
        help_text="URL to return to after portal session",
    )


class CancelSubscriptionSerializer(serializers.Serializer):
    """
    Serializer for subscription cancellation.

    Fields:
        at_period_end: If True, cancel at end of period; if False, immediately

    Usage:
        serializer = CancelSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        StripeService.cancel_subscription(
            subscription=user.subscription,
            at_period_end=serializer.validated_data.get("at_period_end", True)
        )
    """

    at_period_end = serializers.BooleanField(
        default=True,
        help_text="Cancel at period end (True) or immediately (False)",
    )


class InvoiceSerializer(serializers.Serializer):
    """
    Serializer for invoice data from Stripe.

    Fields:
        id: Invoice ID
        amount_cents: Amount in cents
        status: Invoice status
        date: Invoice date
        pdf_url: URL to download PDF

    Usage:
        invoices = StripeService.get_invoices(user)
        serializer = InvoiceSerializer(invoices, many=True)
    """

    id = serializers.CharField(read_only=True)
    amount_cents = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)
    date = serializers.DateTimeField(read_only=True)
    pdf_url = serializers.URLField(read_only=True, allow_null=True)
