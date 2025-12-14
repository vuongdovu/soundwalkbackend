"""
Abstract base strategy for payment processing.

This module defines the strategy pattern contract that all payment
strategies must implement. Each strategy handles a specific payment
flow (direct, escrow, subscription).

The strategy pattern allows:
- Different payment flows to have different implementations
- Easy extension for new payment types
- Testability through dependency injection

Usage:
    class MyCustomStrategy(PaymentStrategy):
        def create_payment(self, params):
            # Custom implementation
            pass

        def handle_payment_succeeded(self, payment_order, event_data):
            # Custom success handling
            pass

        def handle_payment_failed(self, payment_order, event_data, reason):
            # Custom failure handling
            pass

        def calculate_platform_fee(self, amount_cents):
            return amount_cents * 15 // 100  # 15% fee
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from authentication.models import User
    from payments.models import PaymentOrder

from core.services import ServiceResult


# =============================================================================
# Parameter and Result Types
# =============================================================================


@dataclass
class CreatePaymentParams:
    """
    Parameters for creating a new payment.

    Attributes:
        payer: User making the payment
        amount_cents: Payment amount in smallest currency unit (e.g., cents)
        currency: ISO 4217 currency code (default: 'usd')
        reference_id: UUID of related business entity (e.g., booking ID)
        reference_type: Type of related entity (e.g., 'booking', 'session')
        metadata: Arbitrary key-value pairs for extensibility

    Example:
        params = CreatePaymentParams(
            payer=user,
            amount_cents=5000,
            currency='usd',
            reference_id=booking.id,
            reference_type='booking',
            metadata={'session_count': 1},
        )
    """

    payer: User
    amount_cents: int
    currency: str = "usd"
    reference_id: uuid.UUID | None = None
    reference_type: str | None = None
    metadata: dict[str, Any] | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate parameters after initialization."""
        if self.amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if not self.currency:
            raise ValueError("currency is required")


@dataclass
class PaymentResult:
    """
    Result from creating a payment.

    Returned by PaymentStrategy.create_payment() on success.
    Contains the created PaymentOrder and the client_secret needed
    for frontend payment completion.

    Attributes:
        payment_order: The created PaymentOrder instance
        client_secret: Stripe client_secret for frontend confirmation

    Usage:
        result = strategy.create_payment(params)
        if result.success:
            # Return client_secret to frontend
            return {
                'payment_order_id': result.data.payment_order.id,
                'client_secret': result.data.client_secret,
            }
    """

    payment_order: PaymentOrder
    client_secret: str


# =============================================================================
# Abstract Strategy
# =============================================================================


class PaymentStrategy(ABC):
    """
    Abstract base class for payment processing strategies.

    Each strategy implements a specific payment flow:
    - DirectPaymentStrategy: Immediate capture and settlement
    - EscrowPaymentStrategy: Hold funds until service completion
    - SubscriptionPaymentStrategy: Recurring payments via Stripe Subscription

    The strategy pattern allows the PaymentOrchestrator to delegate
    payment processing to the appropriate strategy based on the
    PaymentOrder's strategy_type.

    All strategies share the same interface but differ in:
    - State transitions (direct skips HELD, escrow uses HELD)
    - Ledger entries (direct settles immediately, escrow holds first)
    - Webhook handling (different success/failure flows)

    Subclasses must implement all abstract methods.
    """

    @abstractmethod
    def create_payment(
        self, params: CreatePaymentParams
    ) -> ServiceResult[PaymentResult]:
        """
        Create a new payment and return the client secret.

        Creates a PaymentOrder in DRAFT state, calls Stripe to create
        a PaymentIntent, stores the PaymentIntent ID, transitions to
        PENDING state, and returns the client_secret for frontend use.

        Args:
            params: Payment creation parameters

        Returns:
            ServiceResult containing PaymentResult with payment_order
            and client_secret on success, or error details on failure.

        Raises:
            StripeError: If Stripe API call fails (wrapped in ServiceResult)

        Example:
            result = strategy.create_payment(params)
            if result.success:
                return JsonResponse({
                    'client_secret': result.data.client_secret,
                })
            return JsonResponse(result.to_response(), status=400)
        """

    @abstractmethod
    def handle_payment_succeeded(
        self,
        payment_order: PaymentOrder,
        event_data: dict[str, Any],
    ) -> ServiceResult[PaymentOrder]:
        """
        Process a successful payment webhook event.

        Called when Stripe sends payment_intent.succeeded webhook.
        Handles state transitions and ledger entries atomically.

        The implementation should:
        1. Transition PaymentOrder through appropriate states
        2. Record ledger entries for funds movement
        3. Save the updated PaymentOrder

        Args:
            payment_order: The PaymentOrder to update
            event_data: Full webhook event data from Stripe

        Returns:
            ServiceResult containing updated PaymentOrder on success,
            or error details on failure.

        Note:
            This method should be called within a database transaction
            with select_for_update to prevent race conditions.
        """

    @abstractmethod
    def handle_payment_failed(
        self,
        payment_order: PaymentOrder,
        event_data: dict[str, Any],
        reason: str,
    ) -> ServiceResult[PaymentOrder]:
        """
        Process a failed payment webhook event.

        Called when Stripe sends payment_intent.payment_failed webhook.
        Transitions the PaymentOrder to FAILED state with failure reason.

        Args:
            payment_order: The PaymentOrder to update
            event_data: Full webhook event data from Stripe
            reason: Human-readable failure reason

        Returns:
            ServiceResult containing updated PaymentOrder on success,
            or error details on failure.
        """

    @abstractmethod
    def calculate_platform_fee(self, amount_cents: int) -> int:
        """
        Calculate the platform fee for a given payment amount.

        Args:
            amount_cents: Total payment amount in cents

        Returns:
            Platform fee amount in cents

        Example:
            # 15% platform fee
            fee = strategy.calculate_platform_fee(10000)  # Returns 1500
        """
