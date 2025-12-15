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

    # Create an escrow payment (requires recipient_profile_id in metadata)
    from payments.strategies import EscrowPaymentStrategy

    strategy = EscrowPaymentStrategy()
    result = strategy.create_payment(
        CreatePaymentParams(
            payer=user,
            amount_cents=10000,
            currency='usd',
            reference_id=session.id,
            reference_type='session',
            metadata={
                'recipient_profile_id': str(mentor.profile.id),
            },
        )
    )
"""

from payments.strategies.base import (
    CreatePaymentParams,
    PaymentResult,
    PaymentStrategy,
)
from payments.strategies.direct import DirectPaymentStrategy
from payments.strategies.escrow import EscrowPaymentStrategy
from payments.strategies.subscription import (
    CreateSubscriptionParams,
    SubscriptionCreationResult,
    SubscriptionPaymentStrategy,
)

__all__ = [
    "CreatePaymentParams",
    "CreateSubscriptionParams",
    "DirectPaymentStrategy",
    "EscrowPaymentStrategy",
    "PaymentResult",
    "PaymentStrategy",
    "SubscriptionCreationResult",
    "SubscriptionPaymentStrategy",
]
