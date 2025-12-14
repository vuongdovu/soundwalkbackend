"""
Direct payment strategy for immediate capture and settlement.

This strategy implements the simplest payment flow:
1. Customer initiates payment
2. PaymentIntent created via Stripe
3. Customer completes payment (card input, 3DS, etc.)
4. Webhook confirms success
5. Funds are immediately settled to platform (no escrow hold)

State Flow:
    DRAFT -> PENDING -> PROCESSING -> CAPTURED -> SETTLED

Ledger Flow on Success:
    Entry 1: Debit EXTERNAL_STRIPE, Credit PLATFORM_ESCROW (full amount)
    Entry 2: Debit PLATFORM_ESCROW, Credit PLATFORM_REVENUE (platform fee)

The platform fee is calculated as a percentage of the payment amount,
configured via PLATFORM_FEE_PERCENT setting (default: 15%).

Usage:
    from payments.strategies import DirectPaymentStrategy, CreatePaymentParams

    strategy = DirectPaymentStrategy()
    result = strategy.create_payment(
        CreatePaymentParams(
            payer=user,
            amount_cents=10000,
            currency='usd',
            reference_id=session.id,
            reference_type='session',
        )
    )

    if result.success:
        # Return client_secret to frontend
        return {
            'payment_order_id': result.data.payment_order.id,
            'client_secret': result.data.client_secret,
        }
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction

from core.services import ServiceResult

from payments.adapters import (
    CreatePaymentIntentParams,
    IdempotencyKeyGenerator,
    StripeAdapter,
)
from payments.exceptions import (
    StripeError,
)
from payments.ledger.models import AccountType, EntryType
from payments.ledger.services import LedgerService
from payments.ledger.types import RecordEntryParams
from payments.models import PaymentOrder
from payments.state_machines import PaymentOrderState, PaymentStrategyType
from payments.strategies.base import (
    CreatePaymentParams,
    PaymentResult,
    PaymentStrategy,
)

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


class DirectPaymentStrategy(PaymentStrategy):
    """
    Strategy for direct payment with immediate capture and settlement.

    Direct payments skip the escrow hold phase - after successful capture,
    funds are immediately available to the platform. This is appropriate
    for simple purchases where no service delivery period is needed.

    State Flow:
        DRAFT -> PENDING -> PROCESSING -> CAPTURED -> SETTLED

    Platform Fee:
        A configurable percentage (PLATFORM_FEE_PERCENT) is deducted from
        each payment and moved to PLATFORM_REVENUE. The remaining amount
        stays in PLATFORM_ESCROW (for potential future payout or refund).

    Dependency Injection:
        The Stripe adapter can be injected for testing. If not provided,
        uses the default StripeAdapter class.

    Usage:
        # Production
        strategy = DirectPaymentStrategy()

        # Testing with mock adapter
        strategy = DirectPaymentStrategy(stripe_adapter=MockStripeAdapter)
    """

    def __init__(self, stripe_adapter: type | None = None):
        """
        Initialize the strategy with optional Stripe adapter injection.

        Args:
            stripe_adapter: Optional Stripe adapter class for dependency injection.
                            If not provided, uses the default StripeAdapter.
        """
        self.stripe = stripe_adapter or StripeAdapter

    def create_payment(
        self, params: CreatePaymentParams
    ) -> ServiceResult[PaymentResult]:
        """
        Create a direct payment and return the client secret.

        Steps:
        1. Create PaymentOrder in DRAFT state
        2. Call Stripe to create a PaymentIntent
        3. Store the PaymentIntent ID on the order
        4. Transition to PENDING state
        5. Return client_secret for frontend use

        The operation is wrapped in a transaction. If the Stripe call
        succeeds but subsequent DB operations fail, we have an orphaned
        PaymentIntent on Stripe. This is acceptable because:
        - The PI will expire if not used
        - We can reconcile with Stripe later if needed
        - It's better than having DB inconsistency

        Args:
            params: Payment creation parameters

        Returns:
            ServiceResult containing PaymentResult on success
        """
        logger.info(
            "Creating direct payment",
            extra={
                "payer_id": str(params.payer.id),
                "amount_cents": params.amount_cents,
                "currency": params.currency,
            },
        )

        try:
            with transaction.atomic():
                # Step 1: Create PaymentOrder (DRAFT state)
                order = PaymentOrder.objects.create(
                    payer=params.payer,
                    amount_cents=params.amount_cents,
                    currency=params.currency,
                    strategy_type=PaymentStrategyType.DIRECT,
                    reference_id=params.reference_id,
                    reference_type=params.reference_type,
                    metadata=params.metadata or {},
                )

                # Step 2: Call Stripe to create PaymentIntent
                idempotency_key = IdempotencyKeyGenerator.generate(
                    operation="create_intent",
                    entity_id=order.id,
                )

                stripe_params = CreatePaymentIntentParams(
                    amount_cents=params.amount_cents,
                    currency=params.currency,
                    idempotency_key=idempotency_key,
                    metadata={
                        "payment_order_id": str(order.id),
                        "payer_id": str(params.payer.id),
                    },
                )

                # This call is outside the DB transaction's protection
                # but uses idempotency key for safe retries
                stripe_result = self.stripe.create_payment_intent(stripe_params)

                # Step 3: Store PaymentIntent ID
                order.stripe_payment_intent_id = stripe_result.id
                order.save(update_fields=["stripe_payment_intent_id"])

                # Step 4: Transition to PENDING
                order.submit()
                order.save()

                logger.info(
                    "Direct payment created",
                    extra={
                        "payment_order_id": str(order.id),
                        "payment_intent_id": stripe_result.id,
                        "status": order.state,
                    },
                )

                # Step 5: Return result
                return ServiceResult.success(
                    PaymentResult(
                        payment_order=order,
                        client_secret=stripe_result.client_secret,
                    )
                )

        except StripeError as e:
            logger.error(
                "Failed to create payment: Stripe error",
                extra={
                    "error_code": e.error_code,
                    "is_retryable": e.is_retryable,
                },
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code=e.error_code or "STRIPE_ERROR",
            )

        except Exception as e:
            logger.error(
                f"Failed to create payment: {type(e).__name__}",
                exc_info=True,
            )
            return ServiceResult.failure(
                "An unexpected error occurred while creating the payment",
                error_code="PAYMENT_CREATION_ERROR",
            )

    def handle_payment_succeeded(
        self,
        payment_order: PaymentOrder,
        event_data: dict[str, Any],
    ) -> ServiceResult[PaymentOrder]:
        """
        Process a successful payment webhook event.

        For direct payments, this handles the full flow from PENDING
        through to SETTLED, including ledger entries for the payment
        and platform fee.

        Steps:
        1. Validate current state is PENDING
        2. Transition: PENDING -> PROCESSING -> CAPTURED -> SETTLED
        3. Record ledger entries:
           - Payment received (EXTERNAL_STRIPE -> PLATFORM_ESCROW)
           - Platform fee (PLATFORM_ESCROW -> PLATFORM_REVENUE)
        4. Save the updated order

        The caller (webhook handler) is responsible for:
        - Wrapping this in a transaction with select_for_update
        - Ensuring idempotent webhook processing

        Args:
            payment_order: The PaymentOrder to update
            event_data: Full webhook event data from Stripe

        Returns:
            ServiceResult containing updated PaymentOrder on success
        """
        logger.info(
            "Handling payment success for direct payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
            },
        )

        # Validate we're in the right state
        if payment_order.state != PaymentOrderState.PENDING:
            logger.warning(
                "Payment not in PENDING state",
                extra={
                    "payment_order_id": str(payment_order.id),
                    "current_state": payment_order.state,
                },
            )
            # If already processed, return success (idempotent)
            if payment_order.state in [
                PaymentOrderState.CAPTURED,
                PaymentOrderState.SETTLED,
            ]:
                return ServiceResult.success(payment_order)

            return ServiceResult.failure(
                f"Cannot process payment from state: {payment_order.state}",
                error_code="INVALID_STATE",
            )

        try:
            # State transitions: PENDING -> PROCESSING -> CAPTURED -> SETTLED
            payment_order.process()
            payment_order.capture()
            payment_order.settle_from_captured()
            payment_order.save()

            # Record ledger entries
            self._record_payment_ledger_entries(payment_order)

            logger.info(
                "Direct payment settled successfully",
                extra={
                    "payment_order_id": str(payment_order.id),
                    "final_state": payment_order.state,
                },
            )

            return ServiceResult.success(payment_order)

        except Exception as e:
            logger.error(
                f"Failed to process payment success: {type(e).__name__}",
                extra={"payment_order_id": str(payment_order.id)},
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code="PAYMENT_PROCESSING_ERROR",
            )

    def _record_payment_ledger_entries(self, payment_order: PaymentOrder) -> None:
        """
        Record ledger entries for a successful direct payment.

        Creates two entries atomically:
        1. Payment received: EXTERNAL_STRIPE -> PLATFORM_ESCROW (full amount)
        2. Platform fee: PLATFORM_ESCROW -> PLATFORM_REVENUE (fee amount)

        Uses idempotency keys to ensure entries are only created once,
        even if this method is called multiple times.

        Args:
            payment_order: The settled PaymentOrder
        """
        # Get or create accounts
        external_account = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            currency=payment_order.currency,
            allow_negative=True,  # External can go negative (money out)
        )

        escrow_account = LedgerService.get_or_create_account(
            AccountType.PLATFORM_ESCROW,
            owner_id=None,
            currency=payment_order.currency,
        )

        revenue_account = LedgerService.get_or_create_account(
            AccountType.PLATFORM_REVENUE,
            owner_id=None,
            currency=payment_order.currency,
        )

        # Calculate platform fee
        platform_fee = self.calculate_platform_fee(payment_order.amount_cents)

        # Create ledger entries atomically
        entries = []

        # Entry 1: Payment received from Stripe
        entries.append(
            RecordEntryParams(
                debit_account_id=external_account.id,
                credit_account_id=escrow_account.id,
                amount_cents=payment_order.amount_cents,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=f"payment:{payment_order.id}:received",
                reference_type="payment_order",
                reference_id=payment_order.id,
                description=f"Payment received for order {payment_order.id}",
                created_by="direct_payment_strategy",
            )
        )

        # Entry 2: Platform fee collection (only if fee > 0)
        if platform_fee > 0:
            entries.append(
                RecordEntryParams(
                    debit_account_id=escrow_account.id,
                    credit_account_id=revenue_account.id,
                    amount_cents=platform_fee,
                    entry_type=EntryType.FEE_COLLECTED,
                    idempotency_key=f"payment:{payment_order.id}:fee",
                    reference_type="payment_order",
                    reference_id=payment_order.id,
                    description=f"Platform fee for order {payment_order.id}",
                    created_by="direct_payment_strategy",
                )
            )

        LedgerService.record_entries(entries)

        logger.info(
            "Ledger entries recorded for direct payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "amount_cents": payment_order.amount_cents,
                "platform_fee": platform_fee,
                "entries_count": len(entries),
            },
        )

    def handle_payment_failed(
        self,
        payment_order: PaymentOrder,
        event_data: dict[str, Any],
        reason: str,
    ) -> ServiceResult[PaymentOrder]:
        """
        Process a failed payment webhook event.

        Transitions the PaymentOrder to FAILED state with the failure
        reason. The customer can later retry the payment if desired.

        Note: We only fail from PENDING or PROCESSING states.
        If already failed, this is idempotent.

        Args:
            payment_order: The PaymentOrder to update
            event_data: Full webhook event data from Stripe
            reason: Human-readable failure reason

        Returns:
            ServiceResult containing updated PaymentOrder on success
        """
        logger.info(
            "Handling payment failure for direct payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
                "reason": reason,
            },
        )

        # If already failed, return success (idempotent)
        if payment_order.state == PaymentOrderState.FAILED:
            return ServiceResult.success(payment_order)

        # For direct payments, we need to be in PENDING to process
        # PENDING is where we wait for customer to complete payment
        if payment_order.state == PaymentOrderState.PENDING:
            try:
                # Transition through PROCESSING to reach FAILED
                payment_order.process()
                payment_order.fail(reason=reason)
                payment_order.save()

                logger.info(
                    "Direct payment marked as failed",
                    extra={
                        "payment_order_id": str(payment_order.id),
                        "final_state": payment_order.state,
                        "reason": reason,
                    },
                )

                return ServiceResult.success(payment_order)

            except Exception as e:
                logger.error(
                    f"Failed to process payment failure: {type(e).__name__}",
                    extra={"payment_order_id": str(payment_order.id)},
                    exc_info=True,
                )
                return ServiceResult.failure(
                    str(e),
                    error_code="PAYMENT_FAILURE_PROCESSING_ERROR",
                )

        # Can't fail from other states
        logger.warning(
            "Cannot fail payment from current state",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
            },
        )
        return ServiceResult.failure(
            f"Cannot fail payment from state: {payment_order.state}",
            error_code="INVALID_STATE",
        )

    def calculate_platform_fee(self, amount_cents: int) -> int:
        """
        Calculate the platform fee for a given payment amount.

        Uses the PLATFORM_FEE_PERCENT setting (default: 15%).
        Uses integer division to avoid floating-point issues.

        Args:
            amount_cents: Total payment amount in cents

        Returns:
            Platform fee amount in cents

        Example:
            # With 15% platform fee
            fee = strategy.calculate_platform_fee(10000)  # Returns 1500
            fee = strategy.calculate_platform_fee(9999)   # Returns 1499
        """
        fee_percent = getattr(settings, "PLATFORM_FEE_PERCENT", 15)
        return amount_cents * fee_percent // 100
