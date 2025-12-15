"""
Refund service for processing money returns to customers.

This module provides the RefundService class which handles the critical path
for returning money to customers following a two-phase commit pattern for safety.

The service implements:
1. Refund eligibility checking based on PaymentOrder/Payout state
2. Partial and full refund processing
3. Payout cancellation coordination (for RELEASED state refunds)
4. Ledger reversal entries
5. Stripe refund API integration with idempotency

Usage:
    from payments.services import RefundService

    # Check refund eligibility
    eligibility = RefundService.check_refund_eligibility(payment_order)

    if eligibility.eligible:
        # Create a refund
        result = RefundService.create_refund(
            payment_order_id=order.id,
            amount_cents=5000,  # Partial refund
            reason="Customer request",
        )

        if result.success:
            print(f"Refund created: {result.data.refund.id}")
        else:
            print(f"Refund failed: {result.error}")
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Sum

from core.services import BaseService, ServiceResult

from payments.adapters import IdempotencyKeyGenerator, RefundResult, StripeAdapter
from payments.exceptions import (
    LockAcquisitionError,
    PayoutCancellationError,
    StripeAPIUnavailableError,
    StripeError,
    StripeInvalidRequestError,
    StripeRateLimitError,
    StripeTimeoutError,
)
from payments.ledger import LedgerService
from payments.ledger.models import AccountType, EntryType, LedgerEntry
from payments.ledger.types import RecordEntryParams
from payments.locks import DistributedLock
from payments.models import PaymentOrder, Payout, Refund
from payments.state_machines import (
    PaymentOrderState,
    PayoutState,
    RefundState,
)

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Distributed lock TTL for refund execution (seconds)
REFUND_LOCK_TTL = 120

# Lock acquisition timeout (seconds)
REFUND_LOCK_TIMEOUT = 10.0

# States that are NOT refundable (no money moved or terminal states)
NON_REFUNDABLE_STATES = frozenset(
    [
        PaymentOrderState.DRAFT,
        PaymentOrderState.PENDING,
        PaymentOrderState.PROCESSING,
        PaymentOrderState.FAILED,
        PaymentOrderState.CANCELLED,
        PaymentOrderState.REFUNDED,
    ]
)

# States where refunds are allowed
REFUNDABLE_STATES = frozenset(
    [
        PaymentOrderState.CAPTURED,
        PaymentOrderState.HELD,
        PaymentOrderState.RELEASED,
        PaymentOrderState.SETTLED,
        PaymentOrderState.PARTIALLY_REFUNDED,
    ]
)


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class RefundEligibility:
    """
    Result of refund eligibility check.

    Attributes:
        eligible: Whether the payment can be refunded
        fee_refundable: Whether the platform fee can be refunded
        max_refundable_cents: Maximum amount that can be refunded
        requires_payout_cancellation: Whether a payout needs to be cancelled
        payout_to_cancel: The Payout to cancel (if any)
        block_reason: Human-readable reason if not eligible
    """

    eligible: bool
    fee_refundable: bool = False
    max_refundable_cents: int = 0
    requires_payout_cancellation: bool = False
    payout_to_cancel: Payout | None = None
    block_reason: str | None = None


@dataclass
class RefundExecutionResult:
    """
    Result of a refund execution attempt.

    Attributes:
        refund: The Refund model instance
        stripe_refund_id: The Stripe refund ID if successful
        payout_cancelled: Whether a payout was cancelled
        ledger_entries: Ledger entries created for this refund
    """

    refund: Refund
    stripe_refund_id: str | None = None
    payout_cancelled: bool = False
    ledger_entries: list[LedgerEntry] | None = None


# =============================================================================
# Refund Service
# =============================================================================


class RefundService(BaseService):
    """
    Service for processing refunds to customers.

    The RefundService handles the critical path for returning money to customers.
    It implements a two-phase commit pattern to safely interact with Stripe
    while maintaining database consistency.

    Two-Phase Commit Pattern:
        1. Acquire distributed lock to prevent concurrent refunds
        2. Validate eligibility and create Refund record (REQUESTED -> PROCESSING)
        3. Cancel pending payout if needed (within transaction)
        4. Call Stripe create_refund OUTSIDE the transaction
        5. Store stripe_refund_id and record ledger entries
        6. Complete Refund and transition PaymentOrder state

    Eligibility Matrix:
        - DRAFT/PENDING/PROCESSING: Not refundable (no money moved)
        - CAPTURED (Direct): Refundable, fee already taken
        - HELD (Escrow): Refundable, fee can be returned
        - RELEASED with PENDING payout: Refundable, cancel payout first
        - RELEASED with PROCESSING payout: Not refundable (payout in flight)
        - SETTLED with PAID payout: Not refundable (manual intervention)
        - FAILED/CANCELLED/REFUNDED: Not refundable (terminal states)

    Safety Guarantees:
        - Distributed lock prevents concurrent refunds on same payment
        - Two-phase commit ensures Stripe call is never inside a rollback
        - Idempotency key prevents duplicate refunds on retry
        - Ledger idempotency key prevents duplicate ledger entries
    """

    # Stripe adapter - can be injected for testing
    _stripe_adapter: type | None = None

    @classmethod
    def get_stripe_adapter(cls) -> type:
        """Get the Stripe adapter class."""
        return cls._stripe_adapter or StripeAdapter

    @classmethod
    def set_stripe_adapter(cls, adapter: type | None) -> None:
        """Set the Stripe adapter class (for testing)."""
        cls._stripe_adapter = adapter

    # =========================================================================
    # Eligibility Checking
    # =========================================================================

    @classmethod
    def check_refund_eligibility(
        cls,
        payment_order: PaymentOrder,
        amount_cents: int | None = None,
    ) -> RefundEligibility:
        """
        Check whether a payment order can be refunded.

        Evaluates the PaymentOrder state, associated Payout state (if any),
        and the requested refund amount to determine eligibility.

        Args:
            payment_order: The PaymentOrder to check
            amount_cents: Optional specific amount to refund (None = full refund)

        Returns:
            RefundEligibility with detailed eligibility information

        Example:
            eligibility = RefundService.check_refund_eligibility(order)
            if eligibility.eligible:
                if eligibility.requires_payout_cancellation:
                    print(f"Need to cancel payout: {eligibility.payout_to_cancel.id}")
                print(f"Max refundable: ${eligibility.max_refundable_cents / 100}")
            else:
                print(f"Cannot refund: {eligibility.block_reason}")
        """
        cls.get_logger().debug(
            "Checking refund eligibility",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
                "requested_amount_cents": amount_cents,
            },
        )

        # Check if state is refundable
        if payment_order.state in NON_REFUNDABLE_STATES:
            reason = cls._get_non_refundable_reason(payment_order.state)
            return RefundEligibility(
                eligible=False,
                block_reason=reason,
            )

        # Check for existing processing refund (concurrency protection)
        processing_refund = Refund.objects.filter(
            payment_order=payment_order,
            state=RefundState.PROCESSING,
        ).exists()

        if processing_refund:
            return RefundEligibility(
                eligible=False,
                block_reason="A refund is already in progress for this payment",
            )

        # Calculate max refundable amount
        max_refundable, fee_refundable = cls._calculate_refundable_amount(payment_order)

        if max_refundable <= 0:
            return RefundEligibility(
                eligible=False,
                block_reason="No remaining amount to refund",
            )

        # Check payout state for RELEASED/SETTLED orders
        if payment_order.state in [
            PaymentOrderState.RELEASED,
            PaymentOrderState.SETTLED,
        ]:
            payout_check = cls._check_payout_state(payment_order)
            if not payout_check.eligible:
                return payout_check

            return RefundEligibility(
                eligible=True,
                fee_refundable=fee_refundable,
                max_refundable_cents=max_refundable,
                requires_payout_cancellation=payout_check.requires_payout_cancellation,
                payout_to_cancel=payout_check.payout_to_cancel,
            )

        return RefundEligibility(
            eligible=True,
            fee_refundable=fee_refundable,
            max_refundable_cents=max_refundable,
        )

    @classmethod
    def _get_non_refundable_reason(cls, state: str) -> str:
        """Get human-readable reason why a state is not refundable."""
        reasons = {
            PaymentOrderState.DRAFT: "Cannot refund DRAFT order - no money has been moved",
            PaymentOrderState.PENDING: "Cannot refund PENDING order - no money has been moved",
            PaymentOrderState.PROCESSING: "Cannot refund PROCESSING order - payment is in progress",
            PaymentOrderState.FAILED: "Cannot refund FAILED order - terminal state",
            PaymentOrderState.CANCELLED: "Cannot refund CANCELLED order - terminal state",
            PaymentOrderState.REFUNDED: "Payment has already been fully refunded",
        }
        return reasons.get(state, f"Cannot refund order in {state} state")

    @classmethod
    def _calculate_refundable_amount(
        cls,
        payment_order: PaymentOrder,
    ) -> tuple[int, bool]:
        """
        Calculate the maximum refundable amount and whether fee is refundable.

        Args:
            payment_order: The PaymentOrder to calculate for

        Returns:
            Tuple of (max_refundable_cents, fee_refundable)
        """
        original_amount = payment_order.amount_cents

        # Sum of all completed refunds
        completed_refunds = (
            Refund.objects.filter(
                payment_order=payment_order,
                state=RefundState.COMPLETED,
            ).aggregate(total=Sum("amount_cents"))["total"]
            or 0
        )

        max_refundable = original_amount - completed_refunds

        # Fee is only refundable in HELD state (escrow, fee not yet taken)
        fee_refundable = payment_order.state == PaymentOrderState.HELD

        return max_refundable, fee_refundable

    @classmethod
    def _check_payout_state(cls, payment_order: PaymentOrder) -> RefundEligibility:
        """
        Check payout state for RELEASED/SETTLED orders.

        Returns appropriate eligibility based on payout state:
        - PENDING/SCHEDULED: Can cancel and refund
        - PROCESSING: Cannot refund (in flight)
        - PAID: Cannot refund (requires manual intervention)
        """
        # Find the most recent active payout
        payout = (
            Payout.objects.filter(payment_order=payment_order)
            .exclude(state=PayoutState.CANCELLED)
            .order_by("-created_at")
            .first()
        )

        if not payout:
            # No payout exists, can proceed with refund
            return RefundEligibility(eligible=True)

        if payout.state in [PayoutState.PENDING, PayoutState.SCHEDULED]:
            # Can cancel the payout and proceed with refund
            return RefundEligibility(
                eligible=True,
                requires_payout_cancellation=True,
                payout_to_cancel=payout,
            )

        if payout.state == PayoutState.PROCESSING:
            return RefundEligibility(
                eligible=False,
                block_reason=(
                    "Cannot refund - payout is PROCESSING (in flight). "
                    "Wait for payout to complete and retry, or contact support."
                ),
            )

        if payout.state == PayoutState.PAID:
            return RefundEligibility(
                eligible=False,
                block_reason=(
                    "Cannot refund - payout already PAID. "
                    "This requires manual intervention to clawback funds."
                ),
            )

        # FAILED state - can proceed (payout didn't complete)
        return RefundEligibility(eligible=True)

    # =========================================================================
    # Refund Creation
    # =========================================================================

    @classmethod
    def create_refund(
        cls,
        payment_order_id: uuid.UUID,
        amount_cents: int | None = None,
        reason: str | None = None,
        initiated_by: str | None = None,
        attempt: int = 1,
    ) -> ServiceResult[RefundExecutionResult]:
        """
        Create and execute a refund for a payment order.

        This is the main entry point for refund processing. It handles the
        entire flow from validation through Stripe API call, implementing
        the two-phase commit pattern for safety.

        Args:
            payment_order_id: UUID of the PaymentOrder to refund
            amount_cents: Amount to refund (None for full remaining amount)
            reason: Optional reason for the refund
            initiated_by: Optional identifier of who initiated the refund
            attempt: Current attempt number (for idempotency key generation)

        Returns:
            ServiceResult containing RefundExecutionResult on success

        Raises:
            LockAcquisitionError: If unable to acquire distributed lock
            StripeRateLimitError: If Stripe rate limits (transient, retry)
            StripeAPIUnavailableError: If Stripe API unavailable (transient, retry)
            StripeTimeoutError: If Stripe call times out (transient, retry)

        Example:
            result = RefundService.create_refund(
                payment_order_id=order.id,
                amount_cents=5000,
                reason="Customer requested refund",
            )

            if result.success:
                print(f"Refund created: {result.data.refund.id}")
                print(f"Stripe refund: {result.data.stripe_refund_id}")
            else:
                print(f"Refund failed: {result.error}")
        """
        cls.get_logger().info(
            "Starting refund creation",
            extra={
                "payment_order_id": str(payment_order_id),
                "amount_cents": amount_cents,
                "attempt": attempt,
            },
        )

        # Validate amount
        if amount_cents is not None and amount_cents <= 0:
            return ServiceResult.failure(
                "Refund amount must be positive",
                error_code="INVALID_AMOUNT",
            )

        # Step 1: Acquire distributed lock
        lock_key = f"refund:execute:{payment_order_id}"
        try:
            with DistributedLock(
                lock_key, ttl=REFUND_LOCK_TTL, timeout=REFUND_LOCK_TIMEOUT
            ):
                return cls._execute_refund_with_lock(
                    payment_order_id=payment_order_id,
                    amount_cents=amount_cents,
                    reason=reason,
                    initiated_by=initiated_by,
                    attempt=attempt,
                )
        except LockAcquisitionError as e:
            cls.get_logger().warning(
                "Failed to acquire lock for refund execution",
                extra={
                    "payment_order_id": str(payment_order_id),
                    "error": str(e),
                },
            )
            raise

    @classmethod
    def _execute_refund_with_lock(
        cls,
        payment_order_id: uuid.UUID,
        amount_cents: int | None,
        reason: str | None,
        initiated_by: str | None,
        attempt: int,
    ) -> ServiceResult[RefundExecutionResult]:
        """
        Execute refund within the distributed lock.

        Implements the core two-phase commit logic:
        1. Load and validate payment order
        2. Check eligibility
        3. Create Refund record and transition to PROCESSING
        4. Cancel payout if needed
        5. Call Stripe outside transaction
        6. Complete refund and record ledger entries
        """
        # Step 2: Load payment order
        try:
            payment_order = PaymentOrder.objects.select_related("payer").get(
                id=payment_order_id
            )
        except PaymentOrder.DoesNotExist:
            cls.get_logger().error(
                "Payment order not found",
                extra={"payment_order_id": str(payment_order_id)},
            )
            return ServiceResult.failure(
                f"Payment order {payment_order_id} not found",
                error_code="NOT_FOUND",
            )

        # Step 3: Check eligibility
        eligibility = cls.check_refund_eligibility(payment_order, amount_cents)

        if not eligibility.eligible:
            cls.get_logger().warning(
                "Refund not allowed",
                extra={
                    "payment_order_id": str(payment_order_id),
                    "block_reason": eligibility.block_reason,
                },
            )
            return ServiceResult.failure(
                eligibility.block_reason or "Refund not allowed",
                error_code="REFUND_NOT_ALLOWED",
            )

        # Determine refund amount
        refund_amount = amount_cents or eligibility.max_refundable_cents

        # Validate amount doesn't exceed remaining
        if refund_amount > eligibility.max_refundable_cents:
            return ServiceResult.failure(
                f"Refund amount ({refund_amount}) exceeds remaining refundable "
                f"amount ({eligibility.max_refundable_cents})",
                error_code="AMOUNT_EXCEEDS_LIMIT",
            )

        # Determine if this is a full or partial refund
        is_full_refund = refund_amount == eligibility.max_refundable_cents

        # Step 4: Phase 1 - Create Refund record and cancel payout (within transaction)
        cls.get_logger().info(
            "Phase 1: Creating refund record",
            extra={
                "payment_order_id": str(payment_order_id),
                "refund_amount": refund_amount,
                "is_full_refund": is_full_refund,
            },
        )

        payout_cancelled = False
        try:
            with transaction.atomic():
                # Re-fetch with lock
                payment_order = PaymentOrder.objects.select_for_update().get(
                    id=payment_order_id
                )

                # Double-check eligibility under lock
                eligibility = cls.check_refund_eligibility(payment_order, amount_cents)
                if not eligibility.eligible:
                    return ServiceResult.failure(
                        eligibility.block_reason or "Refund no longer allowed",
                        error_code="REFUND_NOT_ALLOWED",
                    )

                # Create Refund record
                refund = Refund.objects.create(
                    payment_order=payment_order,
                    amount_cents=refund_amount,
                    currency=payment_order.currency,
                    reason=reason,
                    metadata={
                        "initiated_by": initiated_by,
                        "attempt": attempt,
                        "is_full_refund": is_full_refund,
                    },
                )

                # Transition to PROCESSING
                refund.process()
                refund.save()

                # Cancel payout if needed
                if (
                    eligibility.requires_payout_cancellation
                    and eligibility.payout_to_cancel
                ):
                    payout = Payout.objects.select_for_update().get(
                        id=eligibility.payout_to_cancel.id
                    )
                    if payout.can_cancel:
                        payout.cancel(reason=f"Cancelled for refund {refund.id}")
                        payout.save()
                        payout_cancelled = True
                        cls.get_logger().info(
                            "Payout cancelled for refund",
                            extra={
                                "refund_id": str(refund.id),
                                "payout_id": str(payout.id),
                            },
                        )
                    else:
                        # State changed, abort
                        raise PayoutCancellationError(
                            f"Cannot cancel payout {payout.id} - state changed to {payout.state}",
                            details={
                                "payout_id": str(payout.id),
                                "payout_state": payout.state,
                            },
                        )

        except PayoutCancellationError as e:
            cls.get_logger().error(
                "Failed to cancel payout for refund",
                extra={
                    "payment_order_id": str(payment_order_id),
                    "error": str(e),
                },
            )
            return ServiceResult.failure(
                str(e), error_code="PAYOUT_CANCELLATION_FAILED"
            )
        except Exception as e:
            cls.get_logger().error(
                f"Failed to create refund record: {type(e).__name__}",
                extra={"payment_order_id": str(payment_order_id)},
                exc_info=True,
            )
            return ServiceResult.failure(
                f"Failed to create refund: {e}",
                error_code="REFUND_CREATION_ERROR",
            )

        # Step 5: Phase 2 - Call Stripe (OUTSIDE transaction)
        cls.get_logger().info(
            "Phase 2: Calling Stripe create_refund",
            extra={
                "refund_id": str(refund.id),
                "payment_intent_id": payment_order.stripe_payment_intent_id,
                "amount_cents": refund_amount,
            },
        )

        stripe_refund_id = None
        try:
            stripe_result = cls._create_stripe_refund(
                payment_order=payment_order,
                refund=refund,
                amount_cents=refund_amount,
                attempt=attempt,
            )
            stripe_refund_id = stripe_result.id
        except (
            StripeRateLimitError,
            StripeAPIUnavailableError,
            StripeTimeoutError,
        ) as e:
            # Transient errors - raise to trigger retry
            # Refund remains in PROCESSING state
            cls.get_logger().warning(
                f"Transient Stripe error, will retry: {type(e).__name__}",
                extra={
                    "refund_id": str(refund.id),
                    "error": str(e),
                    "is_retryable": e.is_retryable,
                },
            )
            raise
        except StripeInvalidRequestError as e:
            # Permanent error - mark refund as failed
            cls.get_logger().error(
                "Stripe invalid request error",
                extra={
                    "refund_id": str(refund.id),
                    "error": str(e),
                },
            )
            cls._fail_refund(refund, str(e))
            return ServiceResult.failure(
                f"Invalid refund request: {e}",
                error_code="STRIPE_INVALID_REQUEST",
            )
        except StripeError as e:
            # Other Stripe errors
            cls.get_logger().error(
                f"Stripe error during refund: {type(e).__name__}",
                extra={
                    "refund_id": str(refund.id),
                    "error": str(e),
                    "is_retryable": e.is_retryable,
                },
            )
            if e.is_retryable:
                raise
            cls._fail_refund(refund, str(e))
            return ServiceResult.failure(str(e), error_code="STRIPE_ERROR")

        # Step 6: Phase 3 - Complete refund and record ledger entries
        cls.get_logger().info(
            "Phase 3: Completing refund and recording ledger entries",
            extra={
                "refund_id": str(refund.id),
                "stripe_refund_id": stripe_refund_id,
            },
        )

        ledger_entries = []
        try:
            with transaction.atomic():
                # Re-fetch with lock
                refund = Refund.objects.select_for_update().get(id=refund.id)
                payment_order = PaymentOrder.objects.select_for_update().get(
                    id=payment_order_id
                )

                # Store Stripe refund ID
                refund.stripe_refund_id = stripe_refund_id

                # Record ledger entries
                ledger_entries = cls._record_refund_ledger_entries(
                    payment_order=payment_order,
                    refund=refund,
                    payout_cancelled=payout_cancelled,
                    eligibility=eligibility,
                )

                # Complete refund
                refund.complete()
                refund.save()

                # Transition payment order state
                if is_full_refund:
                    payment_order.refund_full()
                else:
                    payment_order.refund_partial()
                payment_order.save()

        except Exception as e:
            # Critical: Stripe succeeded but DB failed
            # The refund is stuck in PROCESSING, reconciliation needed
            cls.get_logger().error(
                "Failed to complete refund after Stripe success - reconciliation needed",
                extra={
                    "refund_id": str(refund.id),
                    "stripe_refund_id": stripe_refund_id,
                    "error": str(e),
                },
                exc_info=True,
            )
            # Return success since Stripe refund was created
            return ServiceResult.success(
                RefundExecutionResult(
                    refund=refund,
                    stripe_refund_id=stripe_refund_id,
                    payout_cancelled=payout_cancelled,
                )
            )

        cls.get_logger().info(
            "Refund completed successfully",
            extra={
                "refund_id": str(refund.id),
                "stripe_refund_id": stripe_refund_id,
                "payment_order_state": payment_order.state,
            },
        )

        return ServiceResult.success(
            RefundExecutionResult(
                refund=refund,
                stripe_refund_id=stripe_refund_id,
                payout_cancelled=payout_cancelled,
                ledger_entries=ledger_entries,
            )
        )

    @classmethod
    def _create_stripe_refund(
        cls,
        payment_order: PaymentOrder,
        refund: Refund,
        amount_cents: int,
        attempt: int,
    ) -> RefundResult:
        """
        Create a Stripe refund.

        Args:
            payment_order: The PaymentOrder with stripe_payment_intent_id
            refund: The Refund model instance
            amount_cents: Amount to refund
            attempt: Current attempt number (for idempotency key)

        Returns:
            RefundResult from Stripe

        Raises:
            StripeError: On Stripe API error
        """
        adapter = cls.get_stripe_adapter()

        idempotency_key = IdempotencyKeyGenerator.generate(
            operation="create_refund",
            entity_id=refund.id,
            attempt=attempt,
        )

        return adapter.create_refund(
            payment_intent_id=payment_order.stripe_payment_intent_id,
            idempotency_key=idempotency_key,
            amount_cents=amount_cents,
            reason="requested_by_customer",
            metadata={
                "refund_id": str(refund.id),
                "payment_order_id": str(payment_order.id),
            },
        )

    @classmethod
    def _record_refund_ledger_entries(
        cls,
        payment_order: PaymentOrder,
        refund: Refund,
        payout_cancelled: bool,
        eligibility: RefundEligibility,
    ) -> list[LedgerEntry]:
        """
        Record ledger entries for the refund.

        Pattern A: Standard refund (CAPTURED/SETTLED, fee already taken)
            Debit: PLATFORM_ESCROW
            Credit: EXTERNAL_STRIPE

        Pattern B: HELD refund (fee not taken)
            Same as Pattern A (full amount returned)

        Pattern C: RELEASED refund with payout cancellation
            Entry 1 - Reverse payout allocation:
                Debit: USER_BALANCE[recipient]
                Credit: PLATFORM_ESCROW
            Entry 2 - Refund to customer:
                Debit: PLATFORM_ESCROW
                Credit: EXTERNAL_STRIPE
        """
        entries = []

        # Get accounts
        escrow = LedgerService.get_or_create_account(
            AccountType.PLATFORM_ESCROW,
            owner_id=None,
            currency=payment_order.currency,
        )
        external = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            currency=payment_order.currency,
            allow_negative=True,
        )

        # Pattern C: If payout was cancelled, reverse the payout allocation
        if payout_cancelled and eligibility.payout_to_cancel:
            payout = eligibility.payout_to_cancel
            # Get recipient's user balance account
            recipient_balance = LedgerService.get_or_create_account(
                AccountType.USER_BALANCE,
                owner_id=payout.connected_account.profile.pk,
                currency=payment_order.currency,
            )

            # Record payout reversal
            reversal_entry = LedgerService.record_entry(
                RecordEntryParams(
                    debit_account_id=recipient_balance.id,
                    credit_account_id=escrow.id,
                    amount_cents=payout.amount_cents,
                    entry_type=EntryType.REFUND,
                    idempotency_key=f"refund:{refund.id}:payout_cancel",
                    reference_type="refund",
                    reference_id=refund.id,
                    description=f"Payout reversal for refund {refund.id}",
                    metadata={
                        "payout_id": str(payout.id),
                        "payment_order_id": str(payment_order.id),
                    },
                    created_by="refund_service",
                )
            )
            entries.append(reversal_entry)

        # Record main refund entry (money going back to Stripe/customer)
        refund_entry = LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=escrow.id,
                credit_account_id=external.id,
                amount_cents=refund.amount_cents,
                entry_type=EntryType.REFUND,
                idempotency_key=f"refund:{refund.id}:reversal",
                reference_type="refund",
                reference_id=refund.id,
                description=f"Refund to customer: {refund.amount_cents} cents",
                metadata={
                    "payment_order_id": str(payment_order.id),
                    "stripe_refund_id": refund.stripe_refund_id,
                },
                created_by="refund_service",
            )
        )
        entries.append(refund_entry)

        return entries

    @classmethod
    def _fail_refund(cls, refund: Refund, reason: str) -> None:
        """
        Mark a refund as failed.

        Args:
            refund: The Refund to fail
            reason: Failure reason for audit trail
        """
        try:
            with transaction.atomic():
                refund = Refund.objects.select_for_update().get(id=refund.id)
                if refund.state == RefundState.PROCESSING:
                    refund.fail(reason=reason)
                    refund.save()
                    cls.get_logger().info(
                        "Refund marked as failed",
                        extra={
                            "refund_id": str(refund.id),
                            "reason": reason,
                        },
                    )
        except Exception as e:
            cls.get_logger().error(
                f"Failed to mark refund as failed: {type(e).__name__}",
                extra={
                    "refund_id": str(refund.id),
                    "reason": reason,
                },
                exc_info=True,
            )

    # =========================================================================
    # Query Methods
    # =========================================================================

    @classmethod
    def get_refund(cls, refund_id: uuid.UUID) -> Refund | None:
        """
        Look up a Refund by ID.

        Args:
            refund_id: UUID of the Refund

        Returns:
            Refund if found, None otherwise
        """
        try:
            return Refund.objects.select_related("payment_order").get(id=refund_id)
        except Refund.DoesNotExist:
            return None

    @classmethod
    def get_refunds_for_payment(cls, payment_order_id: uuid.UUID) -> list[Refund]:
        """
        Get all refunds for a payment order.

        Args:
            payment_order_id: UUID of the PaymentOrder

        Returns:
            List of Refund objects ordered by created_at descending
        """
        return list(
            Refund.objects.filter(payment_order_id=payment_order_id).order_by(
                "-created_at"
            )
        )

    @classmethod
    def get_total_refunded(cls, payment_order_id: uuid.UUID) -> int:
        """
        Get total amount already refunded for a payment.

        Args:
            payment_order_id: UUID of the PaymentOrder

        Returns:
            Total refunded amount in cents
        """
        return (
            Refund.objects.filter(
                payment_order_id=payment_order_id,
                state=RefundState.COMPLETED,
            ).aggregate(total=Sum("amount_cents"))["total"]
            or 0
        )


__all__ = [
    "RefundService",
    "RefundEligibility",
    "RefundExecutionResult",
]
