"""
Payment orchestrator service for coordinating payment operations.

This module provides the PaymentOrchestrator class which serves as the
entry point for all payment operations. It coordinates between strategies,
the Stripe adapter, and the ledger.

The orchestrator:
- Routes payments to the appropriate strategy based on strategy_type
- Provides lookup methods for finding PaymentOrders
- Ensures consistent error handling and logging

Usage:
    from payments.services import PaymentOrchestrator, InitiatePaymentParams

    # Initiate a new payment
    result = PaymentOrchestrator.initiate_payment(
        InitiatePaymentParams(
            payer=user,
            amount_cents=5000,
            currency='usd',
            strategy_type=PaymentStrategyType.DIRECT,
        )
    )

    if result.success:
        client_secret = result.data.client_secret
        payment_order = result.data.payment_order
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.services import BaseService, ServiceResult

from payments.models import PaymentOrder
from payments.state_machines import PaymentStrategyType
from payments.strategies import (
    CreatePaymentParams,
    DirectPaymentStrategy,
    PaymentResult,
)

if TYPE_CHECKING:
    from authentication.models import User
    from payments.strategies.base import PaymentStrategy


logger = logging.getLogger(__name__)


# =============================================================================
# Parameter Types
# =============================================================================


@dataclass
class InitiatePaymentParams:
    """
    Parameters for initiating a payment through the orchestrator.

    Attributes:
        payer: User making the payment
        amount_cents: Payment amount in smallest currency unit (e.g., cents)
        currency: ISO 4217 currency code (default: 'usd')
        strategy_type: Payment processing strategy (default: DIRECT)
        reference_id: UUID of related business entity (e.g., booking ID)
        reference_type: Type of related entity (e.g., 'booking', 'session')
        metadata: Arbitrary key-value pairs for extensibility

    Example:
        params = InitiatePaymentParams(
            payer=user,
            amount_cents=10000,
            currency='usd',
            strategy_type=PaymentStrategyType.DIRECT,
            reference_id=session.id,
            reference_type='session',
        )
    """

    payer: User
    amount_cents: int
    currency: str = "usd"
    strategy_type: str = PaymentStrategyType.DIRECT
    reference_id: uuid.UUID | None = None
    reference_type: str | None = None
    metadata: dict[str, Any] | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate parameters after initialization."""
        if self.amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if not self.currency:
            raise ValueError("currency is required")


# =============================================================================
# Payment Orchestrator
# =============================================================================


class PaymentOrchestrator(BaseService):
    """
    Central coordinator for payment operations.

    The orchestrator is the primary entry point for payment operations.
    It delegates to the appropriate strategy based on the payment type
    and provides consistent error handling and logging.

    Strategy Registry:
        - DIRECT: DirectPaymentStrategy (immediate capture/settlement)
        - ESCROW: EscrowPaymentStrategy (future implementation)
        - SUBSCRIPTION: SubscriptionPaymentStrategy (future implementation)

    All methods are class methods - no instance state is maintained.

    Usage:
        # Initiate a payment
        result = PaymentOrchestrator.initiate_payment(params)

        # Look up a payment
        order = PaymentOrchestrator.get_payment_order(payment_order_id)
        order = PaymentOrchestrator.get_payment_by_intent(payment_intent_id)
    """

    # Strategy registry - maps strategy type to strategy class
    STRATEGIES: dict[str, type[PaymentStrategy]] = {
        PaymentStrategyType.DIRECT: DirectPaymentStrategy,
        # Future strategies:
        # PaymentStrategyType.ESCROW: EscrowPaymentStrategy,
        # PaymentStrategyType.SUBSCRIPTION: SubscriptionPaymentStrategy,
    }

    @classmethod
    def get_strategy(cls, strategy_type: str) -> PaymentStrategy:
        """
        Get a strategy instance for the given type.

        Args:
            strategy_type: The strategy type (from PaymentStrategyType enum)

        Returns:
            An instance of the appropriate strategy

        Raises:
            ValueError: If strategy type is not registered
        """
        strategy_class = cls.STRATEGIES.get(strategy_type)
        if not strategy_class:
            supported = ", ".join(cls.STRATEGIES.keys())
            raise ValueError(
                f"Unknown strategy type: {strategy_type}. Supported: {supported}"
            )
        return strategy_class()

    @classmethod
    def initiate_payment(
        cls, params: InitiatePaymentParams
    ) -> ServiceResult[PaymentResult]:
        """
        Initiate a new payment.

        Creates a PaymentOrder, calls Stripe to create a PaymentIntent,
        and returns the client_secret for frontend payment completion.

        The operation delegates to the appropriate strategy based on
        params.strategy_type.

        Args:
            params: Payment initiation parameters

        Returns:
            ServiceResult containing PaymentResult with payment_order
            and client_secret on success, or error details on failure.

        Example:
            result = PaymentOrchestrator.initiate_payment(
                InitiatePaymentParams(
                    payer=user,
                    amount_cents=5000,
                    currency='usd',
                )
            )

            if result.success:
                return {
                    'payment_order_id': result.data.payment_order.id,
                    'client_secret': result.data.client_secret,
                }
        """
        cls.get_logger().info(
            "Initiating payment",
            extra={
                "payer_id": str(params.payer.id),
                "amount_cents": params.amount_cents,
                "currency": params.currency,
                "strategy_type": params.strategy_type,
            },
        )

        try:
            # Get the appropriate strategy
            strategy = cls.get_strategy(params.strategy_type)

            # Convert to strategy params
            strategy_params = CreatePaymentParams(
                payer=params.payer,
                amount_cents=params.amount_cents,
                currency=params.currency,
                reference_id=params.reference_id,
                reference_type=params.reference_type,
                metadata=params.metadata,
            )

            # Delegate to strategy
            result = strategy.create_payment(strategy_params)

            if result.success:
                cls.get_logger().info(
                    "Payment initiated successfully",
                    extra={
                        "payment_order_id": str(result.data.payment_order.id),
                        "strategy_type": params.strategy_type,
                    },
                )
            else:
                cls.get_logger().warning(
                    "Payment initiation failed",
                    extra={
                        "error": result.error,
                        "error_code": result.error_code,
                    },
                )

            return result

        except ValueError as e:
            cls.get_logger().error(f"Invalid payment parameters: {e}")
            return ServiceResult.failure(str(e), error_code="INVALID_PARAMETERS")

        except Exception as e:
            cls.get_logger().error(
                f"Unexpected error initiating payment: {type(e).__name__}",
                exc_info=True,
            )
            return ServiceResult.failure(
                "An unexpected error occurred",
                error_code="PAYMENT_INITIATION_ERROR",
            )

    @classmethod
    def get_payment_order(cls, payment_order_id: uuid.UUID) -> PaymentOrder | None:
        """
        Look up a PaymentOrder by ID.

        Args:
            payment_order_id: UUID of the PaymentOrder

        Returns:
            PaymentOrder if found, None otherwise
        """
        try:
            return PaymentOrder.objects.get(id=payment_order_id)
        except PaymentOrder.DoesNotExist:
            return None

    @classmethod
    def get_payment_by_intent(
        cls, stripe_payment_intent_id: str
    ) -> PaymentOrder | None:
        """
        Look up a PaymentOrder by Stripe PaymentIntent ID.

        Args:
            stripe_payment_intent_id: Stripe PaymentIntent ID (pi_xxx)

        Returns:
            PaymentOrder if found, None otherwise
        """
        try:
            return PaymentOrder.objects.get(
                stripe_payment_intent_id=stripe_payment_intent_id
            )
        except PaymentOrder.DoesNotExist:
            return None

    @classmethod
    def get_strategy_for_order(cls, payment_order: PaymentOrder) -> PaymentStrategy:
        """
        Get the strategy instance for a PaymentOrder.

        Looks up the order's strategy_type and returns the appropriate
        strategy instance.

        Args:
            payment_order: The PaymentOrder

        Returns:
            The appropriate PaymentStrategy instance

        Raises:
            ValueError: If order's strategy_type is not registered
        """
        return cls.get_strategy(payment_order.strategy_type)
