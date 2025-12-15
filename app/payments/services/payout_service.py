"""
Payout service for executing money transfers to connected accounts.

This module provides the PayoutService class which handles the critical path
for money leaving the platform and reaching recipients' Stripe Connected Accounts.

The service implements a two-phase commit pattern for safety:
1. Phase 1: Transition payout to PROCESSING state, commit transaction
2. Phase 2: Call Stripe create_transfer (outside transaction)
3. Phase 3: Store stripe_transfer_id, let webhooks advance state

This pattern ensures that if the Stripe call succeeds but the database write
fails, the payout remains in PROCESSING state and can be reconciled.

Usage:
    from payments.services import PayoutService

    # Execute a single payout
    result = PayoutService.execute_payout(payout_id)

    if result.success:
        print(f"Transfer created: {result.data.stripe_transfer_id}")
    elif result.error_code == "STRIPE_TRANSIENT_ERROR":
        # Retry with exponential backoff
        schedule_retry(payout_id)
    else:
        # Permanent failure
        alert_admin(result.error)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import transaction

from core.services import BaseService, ServiceResult

from payments.adapters import IdempotencyKeyGenerator, StripeAdapter, TransferResult
from payments.exceptions import (
    LockAcquisitionError,
    PaymentNotFoundError,
    PaymentValidationError,
    StripeAPIUnavailableError,
    StripeError,
    StripeInvalidAccountError,
    StripeRateLimitError,
    StripeTimeoutError,
)
from payments.locks import DistributedLock
from payments.models import Payout
from payments.state_machines import PayoutState

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Distributed lock TTL for payout execution (seconds)
PAYOUT_LOCK_TTL = 120

# Lock acquisition timeout (seconds)
PAYOUT_LOCK_TIMEOUT = 10.0

# Maximum retry attempts for transient errors
MAX_PAYOUT_ATTEMPTS = 5


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class PayoutExecutionResult:
    """
    Result of a payout execution attempt.

    Attributes:
        payout: The Payout model instance
        stripe_transfer_id: The Stripe transfer ID if successful
    """

    payout: Payout
    stripe_transfer_id: str | None = None


# =============================================================================
# Payout Service
# =============================================================================


class PayoutService(BaseService):
    """
    Service for executing payouts to connected accounts.

    The PayoutService handles the critical path for money leaving the platform.
    It implements a two-phase commit pattern to safely interact with Stripe
    while maintaining database consistency.

    Two-Phase Commit Pattern:
        1. Acquire distributed lock to prevent concurrent execution
        2. Transition payout to PROCESSING within a transaction
        3. Call Stripe create_transfer OUTSIDE the transaction
        4. Store stripe_transfer_id on success
        5. Let webhooks (transfer.created, transfer.paid) advance state

    Error Handling:
        - Transient errors (rate limits, timeouts): Raise to trigger retry
        - Permanent errors (invalid account): Transition to FAILED
        - Lock contention: Raise LockAcquisitionError

    Safety Guarantees:
        - Distributed lock prevents concurrent execution of same payout
        - Two-phase commit ensures Stripe call is never inside a rollback
        - Idempotency key prevents duplicate transfers on retry
        - Optimistic locking detects concurrent modifications

    Usage:
        # Execute a payout
        result = PayoutService.execute_payout(payout_id)

        # Execute with attempt tracking (for retry logic)
        result = PayoutService.execute_payout(payout_id, attempt=2)
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

    @classmethod
    def execute_payout(
        cls,
        payout_id: uuid.UUID,
        attempt: int = 1,
    ) -> ServiceResult[PayoutExecutionResult]:
        """
        Execute a payout to a connected account.

        This is the main entry point for payout execution. It handles the
        entire flow from validation through Stripe API call, implementing
        the two-phase commit pattern for safety.

        Args:
            payout_id: UUID of the Payout to execute
            attempt: Current attempt number (for idempotency key generation)

        Returns:
            ServiceResult containing PayoutExecutionResult on success

        Raises:
            LockAcquisitionError: If unable to acquire distributed lock
            StripeRateLimitError: If Stripe rate limits (transient, retry)
            StripeAPIUnavailableError: If Stripe API unavailable (transient, retry)
            StripeTimeoutError: If Stripe call times out (transient, retry)

        Note:
            Transient Stripe errors are raised (not returned as failure) to
            allow Celery's retry mechanism to handle them with backoff.
            Permanent errors result in the payout being marked as FAILED.

        Example:
            try:
                result = PayoutService.execute_payout(payout_id)
                if result.success:
                    logger.info(f"Transfer created: {result.data.stripe_transfer_id}")
                else:
                    # Permanent failure
                    logger.error(f"Payout failed: {result.error}")
            except (StripeRateLimitError, StripeAPIUnavailableError, StripeTimeoutError):
                # Transient - retry with backoff
                schedule_retry(payout_id)
        """
        cls.get_logger().info(
            "Starting payout execution",
            extra={
                "payout_id": str(payout_id),
                "attempt": attempt,
            },
        )

        # Step 1: Acquire distributed lock
        lock_key = f"payout:execute:{payout_id}"
        try:
            with DistributedLock(
                lock_key, ttl=PAYOUT_LOCK_TTL, timeout=PAYOUT_LOCK_TIMEOUT
            ):
                return cls._execute_payout_with_lock(payout_id, attempt)
        except LockAcquisitionError as e:
            cls.get_logger().warning(
                "Failed to acquire lock for payout execution",
                extra={
                    "payout_id": str(payout_id),
                    "error": str(e),
                },
            )
            # Re-raise lock errors - caller should handle contention
            raise

    @classmethod
    def _execute_payout_with_lock(
        cls,
        payout_id: uuid.UUID,
        attempt: int,
    ) -> ServiceResult[PayoutExecutionResult]:
        """
        Execute payout within the distributed lock.

        This method implements the core two-phase commit logic:
        1. Load and validate payout
        2. Transition to PROCESSING within transaction
        3. Call Stripe outside transaction
        4. Update with transfer_id

        Args:
            payout_id: UUID of the Payout
            attempt: Current attempt number

        Returns:
            ServiceResult containing PayoutExecutionResult
        """
        # Step 2: Load and validate payout
        try:
            payout = Payout.objects.select_related(
                "connected_account",
                "payment_order",
            ).get(id=payout_id)
        except Payout.DoesNotExist:
            cls.get_logger().error(
                "Payout not found",
                extra={"payout_id": str(payout_id)},
            )
            raise PaymentNotFoundError(
                f"Payout {payout_id} not found",
                details={"payout_id": str(payout_id)},
            )

        # Validate payout is in correct state
        validation_result = cls._validate_payout(payout)
        if not validation_result.success:
            return validation_result

        # Check if already processed (idempotent)
        if payout.state in [
            PayoutState.PROCESSING,
            PayoutState.SCHEDULED,
            PayoutState.PAID,
        ]:
            cls.get_logger().info(
                "Payout already in later state, returning success",
                extra={
                    "payout_id": str(payout_id),
                    "current_state": payout.state,
                },
            )
            return ServiceResult.success(
                PayoutExecutionResult(
                    payout=payout,
                    stripe_transfer_id=payout.stripe_transfer_id,
                )
            )

        # Step 3: Phase 1 - Transition to PROCESSING within transaction
        cls.get_logger().info(
            "Phase 1: Transitioning payout to PROCESSING",
            extra={"payout_id": str(payout_id)},
        )

        try:
            with transaction.atomic():
                # Re-fetch with lock for safety
                payout = Payout.objects.select_for_update().get(id=payout_id)

                # Double-check state under lock
                if payout.state not in [PayoutState.PENDING, PayoutState.SCHEDULED]:
                    cls.get_logger().info(
                        "Payout state changed under lock",
                        extra={
                            "payout_id": str(payout_id),
                            "current_state": payout.state,
                        },
                    )
                    return ServiceResult.success(
                        PayoutExecutionResult(
                            payout=payout,
                            stripe_transfer_id=payout.stripe_transfer_id,
                        )
                    )

                payout.process()
                payout.save()

        except Exception as e:
            cls.get_logger().error(
                f"Failed to transition payout to PROCESSING: {type(e).__name__}",
                extra={"payout_id": str(payout_id)},
                exc_info=True,
            )
            return ServiceResult.failure(
                f"Failed to process payout: {e}",
                error_code="PAYOUT_PROCESSING_ERROR",
            )

        # Step 4: Phase 2 - Call Stripe (OUTSIDE transaction)
        cls.get_logger().info(
            "Phase 2: Calling Stripe create_transfer",
            extra={
                "payout_id": str(payout_id),
                "connected_account_id": payout.connected_account.stripe_account_id,
                "amount_cents": payout.amount_cents,
            },
        )

        try:
            transfer_result = cls._create_stripe_transfer(payout, attempt)
        except (
            StripeRateLimitError,
            StripeAPIUnavailableError,
            StripeTimeoutError,
        ) as e:
            # Transient errors - raise to trigger retry
            # Payout remains in PROCESSING state
            cls.get_logger().warning(
                f"Transient Stripe error, will retry: {type(e).__name__}",
                extra={
                    "payout_id": str(payout_id),
                    "error": str(e),
                    "is_retryable": e.is_retryable,
                },
            )
            raise
        except StripeInvalidAccountError as e:
            # Permanent error - mark payout as failed
            cls.get_logger().error(
                "Connected account is invalid",
                extra={
                    "payout_id": str(payout_id),
                    "connected_account_id": payout.connected_account.stripe_account_id,
                    "error": str(e),
                },
            )
            return cls._fail_payout(payout, str(e))
        except StripeError as e:
            # Other Stripe errors - mark as failed
            cls.get_logger().error(
                f"Stripe error during payout: {type(e).__name__}",
                extra={
                    "payout_id": str(payout_id),
                    "error": str(e),
                    "is_retryable": e.is_retryable,
                },
            )
            if e.is_retryable:
                raise
            return cls._fail_payout(payout, str(e))

        # Step 5: Phase 3 - Store transfer ID
        cls.get_logger().info(
            "Phase 3: Storing stripe_transfer_id",
            extra={
                "payout_id": str(payout_id),
                "stripe_transfer_id": transfer_result.id,
            },
        )

        try:
            with transaction.atomic():
                # Re-fetch to ensure we have latest state
                payout = Payout.objects.select_for_update().get(id=payout_id)

                # Only update if still in PROCESSING (webhook may have advanced it)
                if payout.state == PayoutState.PROCESSING:
                    payout.stripe_transfer_id = transfer_result.id
                    payout.save(update_fields=["stripe_transfer_id", "updated_at"])
                else:
                    cls.get_logger().info(
                        "Payout state advanced by webhook before transfer_id update",
                        extra={
                            "payout_id": str(payout_id),
                            "current_state": payout.state,
                        },
                    )

                # Reload to get fresh data (excluding FSM state field which is protected)
                payout.refresh_from_db(
                    fields=[
                        "stripe_transfer_id",
                        "version",
                        "updated_at",
                        "paid_at",
                        "failed_at",
                        "failure_reason",
                        "scheduled_for",
                        "metadata",
                    ]
                )

        except Exception as e:
            # This is the critical scenario from the architecture doc:
            # Stripe succeeded but our DB write failed.
            # The payout is stuck in PROCESSING but Stripe has the transfer.
            # The reconciliation service will detect and fix this.
            cls.get_logger().error(
                "Failed to store transfer_id after Stripe success - reconciliation needed",
                extra={
                    "payout_id": str(payout_id),
                    "stripe_transfer_id": transfer_result.id,
                    "error": str(e),
                },
                exc_info=True,
            )
            # Return success since Stripe transfer was created
            # The transfer_id may not be saved, but the transfer exists
            return ServiceResult.success(
                PayoutExecutionResult(
                    payout=payout,
                    stripe_transfer_id=transfer_result.id,
                )
            )

        cls.get_logger().info(
            "Payout execution completed successfully",
            extra={
                "payout_id": str(payout_id),
                "stripe_transfer_id": transfer_result.id,
                "final_state": payout.state,
            },
        )

        return ServiceResult.success(
            PayoutExecutionResult(
                payout=payout,
                stripe_transfer_id=transfer_result.id,
            )
        )

    @classmethod
    def _validate_payout(cls, payout: Payout) -> ServiceResult[PayoutExecutionResult]:
        """
        Validate that a payout can be executed.

        Checks:
        1. Payout is in executable state (PENDING or SCHEDULED)
        2. Connected account is ready for payouts
        3. Payout amount is positive

        Args:
            payout: The Payout to validate

        Returns:
            ServiceResult with success or validation error
        """
        # Check payout state
        if payout.state == PayoutState.PAID:
            cls.get_logger().info(
                "Payout already paid, returning success (idempotent)",
                extra={"payout_id": str(payout.id)},
            )
            return ServiceResult.success(
                PayoutExecutionResult(
                    payout=payout,
                    stripe_transfer_id=payout.stripe_transfer_id,
                )
            )

        if payout.state == PayoutState.FAILED:
            cls.get_logger().warning(
                "Payout is in FAILED state - use retry() first",
                extra={"payout_id": str(payout.id)},
            )
            return ServiceResult.failure(
                "Payout is in FAILED state. Use retry() to reset before executing.",
                error_code="PAYOUT_FAILED",
            )

        if payout.state not in [
            PayoutState.PENDING,
            PayoutState.SCHEDULED,
            PayoutState.PROCESSING,
        ]:
            cls.get_logger().warning(
                "Invalid payout state for execution",
                extra={
                    "payout_id": str(payout.id),
                    "current_state": payout.state,
                },
            )
            return ServiceResult.failure(
                f"Cannot execute payout from state: {payout.state}",
                error_code="INVALID_STATE",
            )

        # Check connected account
        connected_account = payout.connected_account
        if not connected_account.is_ready_for_payouts:
            cls.get_logger().warning(
                "Connected account not ready for payouts",
                extra={
                    "payout_id": str(payout.id),
                    "connected_account_id": str(connected_account.id),
                    "onboarding_status": connected_account.onboarding_status,
                    "payouts_enabled": connected_account.payouts_enabled,
                },
            )
            return ServiceResult.failure(
                "Connected account is not ready for payouts. "
                "Onboarding may be incomplete or payouts may be disabled.",
                error_code="ACCOUNT_NOT_READY",
            )

        # Check amount
        if payout.amount_cents <= 0:
            raise PaymentValidationError(
                "Payout amount must be positive",
                details={"amount_cents": payout.amount_cents},
            )

        return ServiceResult.success(None)

    @classmethod
    def _create_stripe_transfer(cls, payout: Payout, attempt: int) -> TransferResult:
        """
        Create a Stripe transfer to the connected account.

        Args:
            payout: The Payout with transfer details
            attempt: Current attempt number (for idempotency key)

        Returns:
            TransferResult from Stripe

        Raises:
            StripeError: On Stripe API error
        """
        adapter = cls.get_stripe_adapter()

        idempotency_key = IdempotencyKeyGenerator.generate(
            operation="create_transfer",
            entity_id=payout.id,
            attempt=attempt,
        )

        return adapter.create_transfer(
            amount_cents=payout.amount_cents,
            currency=payout.currency,
            destination_account_id=payout.connected_account.stripe_account_id,
            idempotency_key=idempotency_key,
            metadata={
                "payout_id": str(payout.id),
                "payment_order_id": str(payout.payment_order_id),
            },
        )

    @classmethod
    def _fail_payout(
        cls, payout: Payout, reason: str
    ) -> ServiceResult[PayoutExecutionResult]:
        """
        Mark a payout as failed with the given reason.

        Args:
            payout: The Payout to fail
            reason: Failure reason for audit trail

        Returns:
            ServiceResult indicating failure
        """
        try:
            with transaction.atomic():
                # Re-fetch with lock
                payout = Payout.objects.select_for_update().get(id=payout.id)

                # Only fail if in a state that allows failure
                if payout.state in [PayoutState.PROCESSING, PayoutState.SCHEDULED]:
                    payout.fail(reason=reason)
                    payout.save()

                    cls.get_logger().info(
                        "Payout marked as failed",
                        extra={
                            "payout_id": str(payout.id),
                            "reason": reason,
                        },
                    )

        except Exception as e:
            cls.get_logger().error(
                f"Failed to mark payout as failed: {type(e).__name__}",
                extra={
                    "payout_id": str(payout.id),
                    "reason": reason,
                },
                exc_info=True,
            )

        return ServiceResult.failure(reason, error_code="PAYOUT_FAILED")

    @classmethod
    def get_payout(cls, payout_id: uuid.UUID) -> Payout | None:
        """
        Look up a Payout by ID.

        Args:
            payout_id: UUID of the Payout

        Returns:
            Payout if found, None otherwise
        """
        try:
            return Payout.objects.select_related(
                "connected_account",
                "payment_order",
            ).get(id=payout_id)
        except Payout.DoesNotExist:
            return None

    @classmethod
    def get_pending_payouts(cls, limit: int = 100) -> list[Payout]:
        """
        Get payouts ready for execution.

        Returns payouts that are:
        - In PENDING state with no scheduled_for, OR
        - In PENDING state with scheduled_for <= now, OR
        - In SCHEDULED state with scheduled_for <= now

        Args:
            limit: Maximum number of payouts to return

        Returns:
            List of Payouts ready for execution
        """
        from django.db.models import Q
        from django.utils import timezone

        now = timezone.now()

        return list(
            Payout.objects.filter(
                Q(state=PayoutState.PENDING, scheduled_for__isnull=True)
                | Q(state=PayoutState.PENDING, scheduled_for__lte=now)
                | Q(state=PayoutState.SCHEDULED, scheduled_for__lte=now)
            )
            .select_related("connected_account", "payment_order")
            .order_by("created_at")[:limit]
        )

    @classmethod
    def get_failed_payouts(
        cls, max_retry_count: int = MAX_PAYOUT_ATTEMPTS, limit: int = 100
    ) -> list[Payout]:
        """
        Get failed payouts eligible for retry.

        Args:
            max_retry_count: Maximum retry attempts before giving up
            limit: Maximum number of payouts to return

        Returns:
            List of failed Payouts that can be retried

        Note:
            The retry_count is tracked via metadata since we don't have
            a dedicated field. This is updated by the worker.
        """
        # Get all failed payouts
        failed_payouts = (
            Payout.objects.filter(
                state=PayoutState.FAILED,
            )
            .select_related(
                "connected_account",
                "payment_order",
            )
            .order_by("failed_at")[:limit]
        )

        # Filter by retry count in metadata
        eligible = []
        for payout in failed_payouts:
            retry_count = payout.metadata.get("retry_count", 0)
            if retry_count < max_retry_count:
                eligible.append(payout)

        return eligible


__all__ = [
    "PayoutService",
    "PayoutExecutionResult",
]
