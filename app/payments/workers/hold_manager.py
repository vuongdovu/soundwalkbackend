"""
Hold manager worker for processing expired escrow holds.

This module provides Celery tasks for automatic processing of expired
FundHolds according to the auto-release policy (release to recipient).

Tasks:
- process_expired_holds: Periodic task that scans for and queues expired holds
- release_single_hold: Task that releases a single hold with distributed locking

Usage:
    # Typically called via celery-beat schedule
    from payments.workers import process_expired_holds

    # Or manually trigger processing
    process_expired_holds.delay()

    # Release a specific hold
    release_single_hold.delay(str(fund_hold.id), reason="service_completed")
"""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task
from django.utils import timezone

from payments.exceptions import LockAcquisitionError
from payments.locks import DistributedLock
from payments.models import FundHold, PaymentOrder
from payments.state_machines import PaymentOrderState

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Maximum holds to process per batch (prevents memory issues)
BATCH_SIZE = 100

# Lock TTL for release operations (seconds)
RELEASE_LOCK_TTL = 60

# Lock timeout for blocking acquisition (seconds)
RELEASE_LOCK_TIMEOUT = 10.0


# =============================================================================
# Periodic Task: Scan for Expired Holds
# =============================================================================


@shared_task(bind=True)
def process_expired_holds(self) -> dict:
    """
    Scan for expired fund holds and queue release tasks.

    This task runs periodically (via celery-beat) to find holds that
    have passed their expiration time and queues individual release
    tasks for each one.

    The task:
    1. Queries for unreleased holds where expires_at < now
    2. Filters to only holds where payment_order is in HELD state
    3. Orders by expiration time (oldest first)
    4. Queues a release_single_hold task for each expired hold

    Returns:
        Dict with:
        - queued_count: Number of holds queued for release

    Note:
        This task is idempotent. Running it multiple times will not
        double-process holds because release_single_hold checks state
        before releasing.
    """
    logger.info("Starting expired hold scan")

    # Find expired holds that haven't been released
    # Only for orders still in HELD state (not already released/refunded)
    expired_holds = (
        FundHold.objects.filter(
            released=False,
            expires_at__lt=timezone.now(),
            payment_order__state=PaymentOrderState.HELD,
        )
        .select_related("payment_order")
        .order_by("expires_at")[:BATCH_SIZE]
    )

    queued_count = 0
    for hold in expired_holds:
        try:
            # Queue individual release task
            release_single_hold.delay(str(hold.id), reason="expiration")
            queued_count += 1

            logger.info(
                "Queued expired hold for release",
                extra={
                    "fund_hold_id": str(hold.id),
                    "payment_order_id": str(hold.payment_order_id),
                    "expired_at": hold.expires_at.isoformat(),
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to queue hold for release: {e}",
                extra={
                    "fund_hold_id": str(hold.id),
                    "error": str(e),
                },
            )

    logger.info(
        f"Expired hold scan complete: queued {queued_count} holds",
        extra={"queued_count": queued_count},
    )

    return {"queued_count": queued_count}


# =============================================================================
# Individual Release Task
# =============================================================================


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def release_single_hold(self, fund_hold_id: str, reason: str = "expiration") -> dict:
    """
    Release a single FundHold with distributed locking.

    This task:
    1. Validates the hold exists and is in correct state
    2. Acquires a distributed lock to prevent concurrent releases
    3. Calls EscrowPaymentStrategy.release_hold()
    4. Returns success/failure status

    Args:
        fund_hold_id: UUID of the FundHold to release
        reason: Release reason (e.g., "expiration", "service_completed")

    Returns:
        Dict with:
        - status: One of "released", "already_released", "not_found",
                  "invalid_state", "lock_failed", "release_failed"
        - fund_hold_id: The hold ID processed
        - error: Error message if failed
        - error_code: Error code if failed

    Raises:
        Exception: Re-raised to trigger Celery retry for transient failures
    """
    # Import here to avoid circular imports
    from payments.strategies.escrow import EscrowPaymentStrategy

    # Convert string ID to UUID if needed
    if isinstance(fund_hold_id, str):
        try:
            fund_hold_id_uuid = UUID(fund_hold_id)
        except ValueError:
            logger.error(f"Invalid fund_hold_id format: {fund_hold_id}")
            return {
                "status": "not_found",
                "fund_hold_id": fund_hold_id,
                "error": "Invalid UUID format",
            }
    else:
        fund_hold_id_uuid = fund_hold_id

    logger.info(
        "Processing hold release",
        extra={
            "fund_hold_id": str(fund_hold_id),
            "reason": reason,
        },
    )

    # Load the fund hold
    try:
        fund_hold = FundHold.objects.select_related("payment_order").get(
            id=fund_hold_id_uuid
        )
    except FundHold.DoesNotExist:
        logger.warning(
            "FundHold not found",
            extra={"fund_hold_id": str(fund_hold_id)},
        )
        return {
            "status": "not_found",
            "fund_hold_id": str(fund_hold_id),
        }

    # Check if already released (idempotency)
    if fund_hold.released:
        logger.info(
            "FundHold already released, skipping",
            extra={
                "fund_hold_id": str(fund_hold_id),
                "released_at": fund_hold.released_at.isoformat()
                if fund_hold.released_at
                else None,
            },
        )
        return {
            "status": "already_released",
            "fund_hold_id": str(fund_hold_id),
        }

    # Check payment order state
    payment_order = fund_hold.payment_order
    if payment_order.state != PaymentOrderState.HELD:
        logger.warning(
            "PaymentOrder not in HELD state",
            extra={
                "fund_hold_id": str(fund_hold_id),
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
            },
        )
        return {
            "status": "invalid_state",
            "fund_hold_id": str(fund_hold_id),
            "current_state": payment_order.state,
        }

    # Acquire distributed lock for the release operation
    lock_key = f"escrow:release:{payment_order.id}"

    try:
        with DistributedLock(
            lock_key, ttl=RELEASE_LOCK_TTL, blocking=True, timeout=RELEASE_LOCK_TIMEOUT
        ):
            # Re-check state under lock (double-check pattern)
            # Use fresh query instead of refresh_from_db to avoid django-fsm issues
            payment_order = PaymentOrder.objects.get(pk=payment_order.pk)
            if payment_order.state != PaymentOrderState.HELD:
                logger.info(
                    "PaymentOrder state changed under lock, skipping",
                    extra={
                        "fund_hold_id": str(fund_hold_id),
                        "payment_order_id": str(payment_order.id),
                        "current_state": payment_order.state,
                    },
                )
                return {
                    "status": "already_released",
                    "fund_hold_id": str(fund_hold_id),
                }

            # Release via strategy
            strategy = EscrowPaymentStrategy()
            result = strategy.release_hold(
                payment_order=payment_order,
                release_reason=reason,
            )

            if result.success:
                logger.info(
                    "Hold released successfully",
                    extra={
                        "fund_hold_id": str(fund_hold_id),
                        "payment_order_id": str(payment_order.id),
                        "reason": reason,
                    },
                )
                return {
                    "status": "released",
                    "fund_hold_id": str(fund_hold_id),
                    "payment_order_id": str(payment_order.id),
                }
            else:
                logger.error(
                    f"Release failed: {result.error}",
                    extra={
                        "fund_hold_id": str(fund_hold_id),
                        "payment_order_id": str(payment_order.id),
                        "error": result.error,
                        "error_code": result.error_code,
                    },
                )
                return {
                    "status": "release_failed",
                    "fund_hold_id": str(fund_hold_id),
                    "error": result.error,
                    "error_code": result.error_code,
                }

    except LockAcquisitionError as e:
        logger.warning(
            f"Could not acquire lock for release: {e}",
            extra={
                "fund_hold_id": str(fund_hold_id),
                "payment_order_id": str(payment_order.id),
                "lock_key": lock_key,
            },
        )
        return {
            "status": "lock_failed",
            "fund_hold_id": str(fund_hold_id),
            "error": str(e),
        }

    except Exception as e:
        logger.exception(
            f"Unexpected error during hold release: {e}",
            extra={
                "fund_hold_id": str(fund_hold_id),
            },
        )
        # Re-raise to trigger Celery retry
        raise


__all__ = [
    "process_expired_holds",
    "release_single_hold",
]
