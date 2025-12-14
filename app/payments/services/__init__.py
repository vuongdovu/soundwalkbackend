"""
Payment services for coordinating payment operations.

This module provides the PaymentOrchestrator which serves as the
entry point for all payment operations. It coordinates between
strategies, the Stripe adapter, and the ledger.

Usage:
    from payments.services import PaymentOrchestrator, InitiatePaymentParams

    # Initiate a new payment
    result = PaymentOrchestrator.initiate_payment(
        InitiatePaymentParams(
            payer=user,
            amount_cents=5000,
            currency='usd',
        )
    )
"""

from payments.services.payment_orchestrator import (
    InitiatePaymentParams,
    PaymentOrchestrator,
)

__all__ = [
    "InitiatePaymentParams",
    "PaymentOrchestrator",
]
