"""
Payout executor worker for processing pending payouts.

This module provides Celery tasks for automatic execution of pending
payouts to connected Stripe accounts.

Tasks:
- process_pending_payouts: Periodic task that scans for and queues pending payouts
- execute_single_payout: Task that executes a single payout with distributed locking
- retry_failed_payouts: Periodic task that retries eligible failed payouts

Usage:
    # Typically called via celery-beat schedule
    from payments.workers import process_pending_payouts

    # Or manually trigger processing
    process_pending_payouts.delay()

    # Execute a specific payout
    execute_single_payout.delay(str(payout.id))

    # Retry failed payouts
    retry_failed_payouts.delay()
"""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task
from django.db import models, transaction
from django.utils import timezone

from payments.exceptions import (
    LockAcquisitionError,
    StripeAPIUnavailableError,
    StripeRateLimitError,
    StripeTimeoutError,
)
from payments.models import Payout
from payments.state_machines import PayoutState

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Maximum payouts to process per batch (prevents memory issues)
BATCH_SIZE = 100

# Maximum retry attempts for failed payouts
MAX_RETRY_ATTEMPTS = 5

# Failure reasons that are retryable
RETRYABLE_FAILURE_REASONS = [
    "rate_limit",
    "api_unavailable",
    "timeout",
    "connection_error",
    "temporary_error",
]


# =============================================================================
# Periodic Task: Scan for Pending Payouts
# =============================================================================


@shared_task(bind=True)
def process_pending_payouts(self) -> dict:
    """
    Scan for pending payouts and queue execution tasks.

    This task runs periodically (via celery-beat) to find payouts that
    are ready for execution and queues individual execution tasks for each.

    The task:
    1. Queries for PENDING payouts where scheduled_for <= now (or NULL)
    2. Orders by creation time (oldest first)
    3. Queues an execute_single_payout task for each pending payout

    Returns:
        Dict with:
        - queued_count: Number of payouts queued for execution

    Note:
        This task is idempotent. Running it multiple times will not
        double-process payouts because execute_single_payout checks state
        before executing.
    """
    logger.info("Starting pending payout scan")

    now = timezone.now()

    # Find pending payouts that are ready for execution
    # Either scheduled_for is in the past or scheduled_for is NULL
    pending_payouts = (
        Payout.objects.filter(state=PayoutState.PENDING)
        .filter(
            # Either no schedule (immediate) or schedule has passed
            models.Q(scheduled_for__isnull=True) | models.Q(scheduled_for__lte=now)
        )
        .select_related("connected_account")
        .order_by("created_at")[:BATCH_SIZE]
    )

    queued_count = 0
    for payout in pending_payouts:
        try:
            # Queue individual execution task
            execute_single_payout.delay(str(payout.id), attempt=1)
            queued_count += 1

            logger.info(
                "Queued pending payout for execution",
                extra={
                    "payout_id": str(payout.id),
                    "payment_order_id": str(payout.payment_order_id),
                    "amount_cents": payout.amount_cents,
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to queue payout for execution: {e}",
                extra={
                    "payout_id": str(payout.id),
                    "error": str(e),
                },
            )

    logger.info(
        f"Pending payout scan complete: queued {queued_count} payouts",
        extra={"queued_count": queued_count},
    )

    return {"queued_count": queued_count}


# =============================================================================
# Individual Execution Task
# =============================================================================


@shared_task(
    bind=True,
    autoretry_for=(StripeRateLimitError, StripeAPIUnavailableError, StripeTimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_kwargs={"max_retries": MAX_RETRY_ATTEMPTS},
    acks_late=True,
)
def execute_single_payout(self, payout_id: str, attempt: int = 1) -> dict:
    """
    Execute a single payout with distributed locking.

    This task:
    1. Validates the payout exists and is in correct state
    2. Calls PayoutService.execute_payout()
    3. Returns success/failure status

    The PayoutService handles:
    - Distributed locking
    - Two-phase commit pattern
    - Stripe API calls
    - Error handling and state transitions

    Args:
        payout_id: UUID of the Payout to execute
        attempt: Current attempt number (for logging)

    Returns:
        Dict with:
        - status: One of "executed", "already_processed", "not_found",
                  "invalid_state", "failed", "lock_failed"
        - payout_id: The payout ID processed
        - stripe_transfer_id: Stripe transfer ID if successful
        - error: Error message if failed
        - error_code: Error code if failed

    Raises:
        StripeRateLimitError: Re-raised to trigger Celery retry
        StripeAPIUnavailableError: Re-raised to trigger Celery retry
        StripeTimeoutError: Re-raised to trigger Celery retry
    """
    from payments.services import PayoutService

    # Convert string ID to UUID if needed
    if isinstance(payout_id, str):
        try:
            payout_id_uuid = UUID(payout_id)
        except ValueError:
            logger.error(f"Invalid payout_id format: {payout_id}")
            return {
                "status": "not_found",
                "payout_id": payout_id,
                "error": "Invalid UUID format",
            }
    else:
        payout_id_uuid = payout_id

    logger.info(
        "Processing payout execution",
        extra={
            "payout_id": str(payout_id),
            "attempt": attempt,
            "celery_retries": self.request.retries,
        },
    )

    # Load the payout to check state first
    try:
        payout = Payout.objects.select_related("connected_account").get(
            id=payout_id_uuid
        )
    except Payout.DoesNotExist:
        logger.warning(
            "Payout not found",
            extra={"payout_id": str(payout_id)},
        )
        return {
            "status": "not_found",
            "payout_id": str(payout_id),
        }

    # Check if already processed (idempotency)
    if payout.state in [
        PayoutState.PROCESSING,
        PayoutState.SCHEDULED,
        PayoutState.PAID,
    ]:
        logger.info(
            "Payout already processed, skipping",
            extra={
                "payout_id": str(payout_id),
                "current_state": payout.state,
                "stripe_transfer_id": payout.stripe_transfer_id,
            },
        )
        return {
            "status": "already_processed",
            "payout_id": str(payout_id),
            "current_state": payout.state,
            "stripe_transfer_id": payout.stripe_transfer_id,
        }

    # Check if in correct state for execution
    if payout.state not in [PayoutState.PENDING, PayoutState.SCHEDULED]:
        logger.warning(
            "Payout not in executable state",
            extra={
                "payout_id": str(payout_id),
                "current_state": payout.state,
            },
        )
        return {
            "status": "invalid_state",
            "payout_id": str(payout_id),
            "current_state": payout.state,
        }

    # Execute via PayoutService
    try:
        result = PayoutService.execute_payout(payout_id_uuid, attempt=attempt)

        if result.success:
            logger.info(
                "Payout executed successfully",
                extra={
                    "payout_id": str(payout_id),
                    "stripe_transfer_id": result.data.stripe_transfer_id,
                    "amount_cents": result.data.payout.amount_cents,
                },
            )
            return {
                "status": "executed",
                "payout_id": str(payout_id),
                "stripe_transfer_id": result.data.stripe_transfer_id,
            }
        else:
            logger.error(
                f"Payout execution failed: {result.error}",
                extra={
                    "payout_id": str(payout_id),
                    "error": result.error,
                    "error_code": result.error_code,
                },
            )
            return {
                "status": "failed",
                "payout_id": str(payout_id),
                "error": result.error,
                "error_code": result.error_code,
            }

    except LockAcquisitionError as e:
        logger.warning(
            f"Could not acquire lock for payout execution: {e}",
            extra={
                "payout_id": str(payout_id),
            },
        )
        return {
            "status": "lock_failed",
            "payout_id": str(payout_id),
            "error": str(e),
        }

    except (StripeRateLimitError, StripeAPIUnavailableError, StripeTimeoutError):
        # Re-raise to trigger Celery retry
        logger.warning(
            "Transient Stripe error, will retry",
            extra={
                "payout_id": str(payout_id),
                "attempt": attempt,
                "celery_retries": self.request.retries,
            },
        )
        raise

    except Exception as e:
        logger.exception(
            f"Unexpected error during payout execution: {e}",
            extra={
                "payout_id": str(payout_id),
            },
        )
        return {
            "status": "failed",
            "payout_id": str(payout_id),
            "error": str(e),
            "error_code": "UNEXPECTED_ERROR",
        }


# =============================================================================
# Periodic Task: Retry Failed Payouts
# =============================================================================


@shared_task(bind=True)
def retry_failed_payouts(self) -> dict:
    """
    Scan for failed payouts and queue retries for eligible ones.

    This task runs periodically (via celery-beat) to find failed payouts
    that have retryable failure reasons and haven't exceeded max retries.

    The task:
    1. Queries for FAILED payouts
    2. Filters to payouts with retryable failure reasons
    3. Filters out payouts that have exceeded max retry attempts
    4. Transitions eligible payouts to PENDING and queues execution

    Returns:
        Dict with:
        - queued_count: Number of payouts queued for retry
        - skipped_count: Number of payouts skipped (non-retryable or max retries)

    Note:
        Retry tracking is done via the metadata field on the Payout model.
    """
    logger.info("Starting failed payout retry scan")

    # Find failed payouts
    failed_payouts = (
        Payout.objects.filter(state=PayoutState.FAILED)
        .select_related("connected_account", "payment_order")
        .order_by("failed_at")[:BATCH_SIZE]
    )

    queued_count = 0
    skipped_count = 0

    for payout in failed_payouts:
        # Check retry count from metadata
        retry_count = payout.metadata.get("retry_count", 0)

        if retry_count >= MAX_RETRY_ATTEMPTS:
            logger.info(
                "Payout exceeded max retries, skipping",
                extra={
                    "payout_id": str(payout.id),
                    "retry_count": retry_count,
                },
            )
            skipped_count += 1
            continue

        # Check if failure reason is retryable
        failure_reason = payout.failure_reason or ""
        is_retryable = any(
            reason in failure_reason.lower() for reason in RETRYABLE_FAILURE_REASONS
        )

        if not is_retryable:
            logger.info(
                "Payout failure not retryable, skipping",
                extra={
                    "payout_id": str(payout.id),
                    "failure_reason": failure_reason,
                },
            )
            skipped_count += 1
            continue

        try:
            # Transition to PENDING and increment retry count
            with transaction.atomic():
                payout = Payout.objects.select_for_update().get(id=payout.id)

                # Double-check state under lock
                if payout.state != PayoutState.FAILED:
                    logger.info(
                        "Payout state changed, skipping",
                        extra={
                            "payout_id": str(payout.id),
                            "current_state": payout.state,
                        },
                    )
                    skipped_count += 1
                    continue

                # Transition to PENDING (clears failure info)
                payout.retry()

                # Update retry count in metadata
                payout.metadata["retry_count"] = retry_count + 1
                payout.save()

            # Queue execution task
            execute_single_payout.delay(str(payout.id), attempt=retry_count + 2)
            queued_count += 1

            logger.info(
                "Queued failed payout for retry",
                extra={
                    "payout_id": str(payout.id),
                    "retry_count": retry_count + 1,
                },
            )

        except Exception as e:
            logger.error(
                f"Failed to queue payout for retry: {e}",
                extra={
                    "payout_id": str(payout.id),
                    "error": str(e),
                },
            )
            skipped_count += 1

    logger.info(
        f"Failed payout retry scan complete: queued {queued_count}, skipped {skipped_count}",
        extra={"queued_count": queued_count, "skipped_count": skipped_count},
    )

    return {"queued_count": queued_count, "skipped_count": skipped_count}


__all__ = [
    "process_pending_payouts",
    "execute_single_payout",
    "retry_failed_payouts",
]
