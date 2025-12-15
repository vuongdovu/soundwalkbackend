"""
Payment services for coordinating payment operations.

This module provides:
- PaymentOrchestrator: Entry point for payment initiation
- PayoutService: Executes payouts to connected accounts
- RefundService: Processes refunds to customers

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

    # Execute a payout
    from payments.services import PayoutService

    result = PayoutService.execute_payout(payout_id)

    # Create a refund
    from payments.services import RefundService

    result = RefundService.create_refund(
        payment_order_id=order.id,
        amount_cents=2500,
        reason="Customer request",
    )
"""

from payments.services.payment_orchestrator import (
    InitiatePaymentParams,
    PaymentOrchestrator,
)
from payments.services.payout_service import (
    PayoutExecutionResult,
    PayoutService,
)
from payments.services.refund_service import (
    RefundEligibility,
    RefundExecutionResult,
    RefundService,
)

__all__ = [
    "InitiatePaymentParams",
    "PaymentOrchestrator",
    "PayoutExecutionResult",
    "PayoutService",
    "RefundEligibility",
    "RefundExecutionResult",
    "RefundService",
]
