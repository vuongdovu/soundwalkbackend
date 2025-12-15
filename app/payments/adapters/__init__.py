"""
Payment adapters for external services.

This module provides adapters for external payment services like Stripe.
All external payment API calls should go through these adapters to ensure
consistent error handling, timeouts, idempotency, and observability.

Usage:
    from payments.adapters import StripeAdapter, CreatePaymentIntentParams

    # Create a PaymentIntent
    result = StripeAdapter.create_payment_intent(
        CreatePaymentIntentParams(
            amount_cents=5000,
            currency='usd',
            idempotency_key='create:order_123:1',
        )
    )
"""

from payments.adapters.stripe_adapter import (
    CreateCustomerParams,
    CreatePaymentIntentParams,
    CreateSubscriptionParams,
    CustomerResult,
    IdempotencyKeyGenerator,
    PaymentIntentResult,
    RefundResult,
    StripeAdapter,
    SubscriptionResult,
    TransferResult,
    backoff_delay,
    is_retryable_stripe_error,
)

__all__ = [
    "CreateCustomerParams",
    "CreatePaymentIntentParams",
    "CreateSubscriptionParams",
    "CustomerResult",
    "IdempotencyKeyGenerator",
    "PaymentIntentResult",
    "RefundResult",
    "StripeAdapter",
    "SubscriptionResult",
    "TransferResult",
    "backoff_delay",
    "is_retryable_stripe_error",
]
