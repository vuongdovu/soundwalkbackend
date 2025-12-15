"""
Reconciliation worker for periodic state consistency checks.

This module provides Celery tasks for detecting and healing discrepancies
between local payment state and Stripe's source of truth.

Tasks:
- run_scheduled_reconciliation: Periodic task that runs full reconciliation
- reconcile_single_payment_order: On-demand reconciliation for a specific payment
- reconcile_single_payout: On-demand reconciliation for a specific payout

Usage:
    # Typically called via celery-beat schedule
    from payments.workers import run_scheduled_reconciliation

    # Or manually trigger reconciliation
    run_scheduled_reconciliation.delay()

    # Reconcile specific entities
    reconcile_single_payment_order.delay(str(payment_order_id))
    reconcile_single_payout.delay(str(payout_id))

Celery Beat Schedule:
    CELERY_BEAT_SCHEDULE = {
        'reconciliation-hourly': {
            'task': 'payments.workers.reconciliation_worker.run_scheduled_reconciliation',
            'schedule': crontab(minute=15),
            'kwargs': {'lookback_hours': 4, 'stuck_threshold_hours': 2},
        },
        'reconciliation-nightly': {
            'task': 'payments.workers.reconciliation_worker.run_scheduled_reconciliation',
            'schedule': crontab(hour=3, minute=0),
            'kwargs': {'lookback_hours': 48, 'stuck_threshold_hours': 6},
        },
    }
"""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task

from payments.exceptions import ReconciliationLockError

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Default configuration for scheduled runs
DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_STUCK_THRESHOLD_HOURS = 2
DEFAULT_MAX_RECORDS = 500


# =============================================================================
# Periodic Task: Full Reconciliation Run
# =============================================================================


@shared_task(bind=True)
def run_scheduled_reconciliation(
    self,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    stuck_threshold_hours: int = DEFAULT_STUCK_THRESHOLD_HOURS,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> dict:
    """
    Run a full reconciliation pass.

    This task runs periodically (via celery-beat) to detect and heal
    discrepancies between local state and Stripe's source of truth.

    The task:
    1. Acquires a global lock to prevent concurrent runs
    2. Scans PaymentOrders for state discrepancies
    3. Scans Payouts for state discrepancies
    4. Auto-heals clear-cut discrepancies
    5. Flags ambiguous cases for manual review
    6. Records all discrepancies for audit

    Args:
        lookback_hours: How far back to check (default: 24)
        stuck_threshold_hours: Consider records stuck after this (default: 2)
        max_records: Maximum records to check per entity type (default: 500)

    Returns:
        Dict with:
        - status: "completed", "skipped" (lock held), or "failed"
        - run_id: UUID of the reconciliation run
        - payment_orders_checked: Number of PaymentOrders checked
        - payouts_checked: Number of Payouts checked
        - discrepancies_found: Total discrepancies detected
        - auto_healed: Count of auto-healed discrepancies
        - flagged_for_review: Count flagged for manual review
        - failed_to_heal: Count that failed healing
        - error: Error message if failed

    Note:
        If another reconciliation run is in progress, this task will
        return immediately with status "skipped" rather than waiting.
        This prevents queue buildup if runs take longer than expected.
    """
    from payments.services import ReconciliationService

    logger.info(
        "Starting scheduled reconciliation run",
        extra={
            "lookback_hours": lookback_hours,
            "stuck_threshold_hours": stuck_threshold_hours,
            "max_records": max_records,
        },
    )

    try:
        result = ReconciliationService.run_reconciliation(
            lookback_hours=lookback_hours,
            stuck_threshold_hours=stuck_threshold_hours,
            max_records=max_records,
        )

        if result.success:
            run_result = result.data
            logger.info(
                "Scheduled reconciliation completed",
                extra={
                    "run_id": str(run_result.run_id),
                    "payment_orders_checked": run_result.payment_orders_checked,
                    "payouts_checked": run_result.payouts_checked,
                    "discrepancies_found": run_result.discrepancies_found,
                    "auto_healed": run_result.auto_healed,
                    "flagged_for_review": run_result.flagged_for_review,
                    "failed_to_heal": run_result.failed_to_heal,
                },
            )

            return {
                "status": "completed",
                "run_id": str(run_result.run_id),
                "payment_orders_checked": run_result.payment_orders_checked,
                "payouts_checked": run_result.payouts_checked,
                "discrepancies_found": run_result.discrepancies_found,
                "auto_healed": run_result.auto_healed,
                "flagged_for_review": run_result.flagged_for_review,
                "failed_to_heal": run_result.failed_to_heal,
            }
        else:
            logger.error(
                f"Reconciliation failed: {result.error}",
                extra={
                    "error": result.error,
                    "error_code": result.error_code,
                },
            )
            return {
                "status": "failed",
                "error": result.error,
                "error_code": result.error_code,
            }

    except ReconciliationLockError:
        # Another run is in progress - this is expected and OK
        logger.info(
            "Reconciliation run skipped - another run in progress",
            extra={
                "task_id": self.request.id,
            },
        )
        return {
            "status": "skipped",
            "reason": "Another reconciliation run is in progress",
        }

    except Exception as e:
        logger.exception(
            f"Unexpected error during reconciliation: {e}",
            extra={
                "error": str(e),
            },
        )
        return {
            "status": "failed",
            "error": str(e),
            "error_code": "UNEXPECTED_ERROR",
        }


# =============================================================================
# On-Demand Tasks: Single Entity Reconciliation
# =============================================================================


@shared_task(bind=True)
def reconcile_single_payment_order(self, payment_order_id: str) -> dict:
    """
    Reconcile a single PaymentOrder against Stripe.

    Use this for on-demand reconciliation when a specific payment
    is suspected to be out of sync.

    Args:
        payment_order_id: UUID of the PaymentOrder to reconcile

    Returns:
        Dict with:
        - status: "ok", "healed", "flagged", "failed", or "not_found"
        - payment_order_id: The ID processed
        - discrepancy_type: Type of discrepancy if found
        - resolution: How it was resolved
        - action_taken: Description of action taken
        - error: Error message if failed
    """
    from payments.services import ReconciliationService

    logger.info(
        "Reconciling single payment order",
        extra={"payment_order_id": payment_order_id},
    )

    # Convert string ID to UUID
    try:
        payment_order_uuid = UUID(payment_order_id)
    except ValueError:
        logger.error(f"Invalid payment_order_id format: {payment_order_id}")
        return {
            "status": "failed",
            "payment_order_id": payment_order_id,
            "error": "Invalid UUID format",
        }

    try:
        result = ReconciliationService.reconcile_payment_order(payment_order_uuid)

        if not result.success:
            return {
                "status": "not_found"
                if "not found" in result.error.lower()
                else "failed",
                "payment_order_id": payment_order_id,
                "error": result.error,
                "error_code": result.error_code,
            }

        healing_result = result.data

        if healing_result is None:
            # No discrepancy found
            logger.info(
                "Payment order is in sync",
                extra={"payment_order_id": payment_order_id},
            )
            return {
                "status": "ok",
                "payment_order_id": payment_order_id,
                "message": "No discrepancy detected",
            }

        # Discrepancy was found and processed
        logger.info(
            f"Payment order reconciliation complete: {healing_result.resolution}",
            extra={
                "payment_order_id": payment_order_id,
                "discrepancy_type": healing_result.discrepancy.discrepancy_type.value,
                "resolution": healing_result.resolution,
            },
        )

        status = "healed" if healing_result.resolution == "auto_healed" else "flagged"

        return {
            "status": status,
            "payment_order_id": payment_order_id,
            "discrepancy_type": healing_result.discrepancy.discrepancy_type.value,
            "resolution": healing_result.resolution,
            "action_taken": healing_result.action_taken,
            "error": healing_result.error,
        }

    except Exception as e:
        logger.exception(
            f"Error reconciling payment order: {e}",
            extra={"payment_order_id": payment_order_id},
        )
        return {
            "status": "failed",
            "payment_order_id": payment_order_id,
            "error": str(e),
            "error_code": "UNEXPECTED_ERROR",
        }


@shared_task(bind=True)
def reconcile_single_payout(self, payout_id: str) -> dict:
    """
    Reconcile a single Payout against Stripe.

    Use this for on-demand reconciliation when a specific payout
    is suspected to be out of sync, especially after a suspected
    Phase 3 failure.

    Args:
        payout_id: UUID of the Payout to reconcile

    Returns:
        Dict with:
        - status: "ok", "healed", "flagged", "failed", or "not_found"
        - payout_id: The ID processed
        - discrepancy_type: Type of discrepancy if found
        - resolution: How it was resolved
        - action_taken: Description of action taken
        - error: Error message if failed
    """
    from payments.services import ReconciliationService

    logger.info(
        "Reconciling single payout",
        extra={"payout_id": payout_id},
    )

    # Convert string ID to UUID
    try:
        payout_uuid = UUID(payout_id)
    except ValueError:
        logger.error(f"Invalid payout_id format: {payout_id}")
        return {
            "status": "failed",
            "payout_id": payout_id,
            "error": "Invalid UUID format",
        }

    try:
        result = ReconciliationService.reconcile_payout(payout_uuid)

        if not result.success:
            return {
                "status": "not_found"
                if "not found" in result.error.lower()
                else "failed",
                "payout_id": payout_id,
                "error": result.error,
                "error_code": result.error_code,
            }

        healing_result = result.data

        if healing_result is None:
            # No discrepancy found
            logger.info(
                "Payout is in sync",
                extra={"payout_id": payout_id},
            )
            return {
                "status": "ok",
                "payout_id": payout_id,
                "message": "No discrepancy detected",
            }

        # Discrepancy was found and processed
        logger.info(
            f"Payout reconciliation complete: {healing_result.resolution}",
            extra={
                "payout_id": payout_id,
                "discrepancy_type": healing_result.discrepancy.discrepancy_type.value,
                "resolution": healing_result.resolution,
            },
        )

        status = "healed" if healing_result.resolution == "auto_healed" else "flagged"

        return {
            "status": status,
            "payout_id": payout_id,
            "discrepancy_type": healing_result.discrepancy.discrepancy_type.value,
            "resolution": healing_result.resolution,
            "action_taken": healing_result.action_taken,
            "error": healing_result.error,
        }

    except Exception as e:
        logger.exception(
            f"Error reconciling payout: {e}",
            extra={"payout_id": payout_id},
        )
        return {
            "status": "failed",
            "payout_id": payout_id,
            "error": str(e),
            "error_code": "UNEXPECTED_ERROR",
        }


__all__ = [
    "run_scheduled_reconciliation",
    "reconcile_single_payment_order",
    "reconcile_single_payout",
]
