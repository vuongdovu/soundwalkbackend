"""
Payment strategies for different payment flows.

This module provides the strategy pattern implementation for handling
different types of payments (direct, escrow, subscription).

Usage:
    from payments.strategies import DirectPaymentStrategy, CreatePaymentParams

    # Create a direct payment
    strategy = DirectPaymentStrategy()
    result = strategy.create_payment(
        CreatePaymentParams(
            payer=user,
            amount_cents=5000,
            currency='usd',
        )
    )
"""

from payments.strategies.base import (
    CreatePaymentParams,
    PaymentResult,
    PaymentStrategy,
)
from payments.strategies.direct import DirectPaymentStrategy

__all__ = [
    "CreatePaymentParams",
    "DirectPaymentStrategy",
    "PaymentResult",
    "PaymentStrategy",
]
