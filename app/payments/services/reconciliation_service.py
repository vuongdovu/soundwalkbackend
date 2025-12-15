"""
Reconciliation service for detecting and healing state discrepancies.

This module provides the ReconciliationService which ensures eventual consistency
between local payment state and Stripe's source of truth. It detects and heals
discrepancies caused by webhook failures, two-phase commit Phase 3 failures,
and race conditions.

Detection Categories:
    1. PaymentOrder discrepancies: Local state behind Stripe PaymentIntent state
    2. Payout discrepancies: Local state behind Stripe Transfer state
    3. Stuck records: Records that have been in PROCESSING too long

Healing Strategy:
    - Auto-heal clear-cut discrepancies (succeeded payments, completed transfers)
    - Flag ambiguous cases for manual review (cancellations, failures)
    - Use distributed locks to prevent races during healing
    - Record ledger ADJUSTMENT entries for audit trail

Usage:
    from payments.services.reconciliation_service import ReconciliationService

    # Run full reconciliation
    result = ReconciliationService.run_reconciliation(
        lookback_hours=24,
        stuck_threshold_hours=2,
    )

    if result.success:
        run_result = result.data
        print(f"Found {run_result.discrepancies_found} discrepancies")
        print(f"Auto-healed: {run_result.auto_healed}")
        print(f"Flagged for review: {run_result.flagged_for_review}")

    # Reconcile single entity
    result = ReconciliationService.reconcile_payment_order(payment_order_id)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from django_fsm import TransitionNotAllowed

from core.services import BaseService, ServiceResult

from payments.adapters import StripeAdapter
from payments.exceptions import (
    HealingError,
    LockAcquisitionError,
    ReconciliationError,
    ReconciliationLockError,
    StripeAPIUnavailableError,
    StripeRateLimitError,
)
from payments.locks import DistributedLock
from payments.models import PaymentOrder, Payout
from payments.models.reconciliation import (
    DiscrepancyResolution,
    ReconciliationDiscrepancy,
    ReconciliationRun,
    ReconciliationRunStatus,
)
from payments.state_machines import PaymentOrderState, PayoutState

if TYPE_CHECKING:
    from typing import Any


logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Default configuration
DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_STUCK_THRESHOLD_HOURS = 2
DEFAULT_MAX_RECORDS = 500

# Lock configuration
RECONCILIATION_RUN_LOCK_TTL = 3600  # 1 hour
RECONCILIATION_RUN_LOCK_TIMEOUT = 5.0  # 5 seconds
HEAL_LOCK_TTL = 60  # 1 minute
HEAL_LOCK_TIMEOUT = 5.0  # 5 seconds


# =============================================================================
# Data Types
# =============================================================================


class DiscrepancyType(str, Enum):
    """Types of discrepancies that can be detected."""

    # PaymentOrder discrepancies
    STRIPE_SUCCEEDED_LOCAL_PROCESSING = "stripe_succeeded_local_processing"
    STRIPE_SUCCEEDED_LOCAL_PENDING = "stripe_succeeded_local_pending"
    STRIPE_FAILED_LOCAL_PROCESSING = "stripe_failed_local_processing"
    STRIPE_CANCELED_LOCAL_ACTIVE = "stripe_canceled_local_active"

    # Payout discrepancies
    STRIPE_TRANSFER_EXISTS_LOCAL_PROCESSING_NO_ID = (
        "stripe_transfer_exists_local_processing_no_id"
    )
    STRIPE_TRANSFER_PAID_LOCAL_PROCESSING = "stripe_transfer_paid_local_processing"
    STRIPE_TRANSFER_PAID_LOCAL_SCHEDULED = "stripe_transfer_paid_local_scheduled"
    STRIPE_TRANSFER_FAILED_LOCAL_PROCESSING = "stripe_transfer_failed_local_processing"

    # Stuck records
    PAYMENT_STUCK_IN_PROCESSING = "payment_stuck_in_processing"
    PAYOUT_STUCK_IN_PROCESSING = "payout_stuck_in_processing"


@dataclass
class Discrepancy:
    """A detected discrepancy between local and Stripe state."""

    discrepancy_type: DiscrepancyType
    entity_type: str  # "payment_order" or "payout"
    entity_id: uuid.UUID
    stripe_id: str | None
    local_state: str
    stripe_state: str | None
    details: dict[str, Any] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=timezone.now)


@dataclass
class HealingResult:
    """Result of attempting to heal a discrepancy."""

    discrepancy: Discrepancy
    resolution: DiscrepancyResolution
    action_taken: str | None = None
    error: str | None = None
    ledger_entry_id: uuid.UUID | None = None


@dataclass
class ReconciliationRunResult:
    """Summary result of a reconciliation run."""

    run_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    payment_orders_checked: int
    payouts_checked: int
    discrepancies_found: int
    auto_healed: int
    flagged_for_review: int
    failed_to_heal: int
    results: list[HealingResult] = field(default_factory=list)


# =============================================================================
# Reconciliation Service
# =============================================================================


class ReconciliationService(BaseService):
    """
    Service for detecting and healing state discrepancies.

    The reconciliation service is the critical safety net that ensures eventual
    consistency between local payment state and Stripe's source of truth.

    Detection Logic:
        The service compares local state against Stripe's state and detects
        discrepancies where local state is behind. Common scenarios include:
        - Webhook failures causing missed state transitions
        - Two-phase commit Phase 3 failures where Stripe succeeded but DB didn't
        - Race conditions between concurrent webhook processing

    Healing Strategy:
        - Clear-cut discrepancies are auto-healed (e.g., Stripe succeeded, we show processing)
        - Ambiguous cases are flagged for manual review (e.g., Stripe canceled)
        - Stuck records (in PROCESSING too long) are flagged for investigation

    Concurrency Safety:
        - Global run lock prevents concurrent reconciliation runs
        - Per-entity locks prevent races during healing
        - Double-check pattern: re-verify discrepancy after acquiring lock

    Usage:
        # Full reconciliation run
        result = ReconciliationService.run_reconciliation()

        # Single entity reconciliation
        result = ReconciliationService.reconcile_payment_order(order_id)
        result = ReconciliationService.reconcile_payout(payout_id)
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
    # Public API
    # =========================================================================

    @classmethod
    def run_reconciliation(
        cls,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
        stuck_threshold_hours: int = DEFAULT_STUCK_THRESHOLD_HOURS,
        max_records: int = DEFAULT_MAX_RECORDS,
    ) -> ServiceResult[ReconciliationRunResult]:
        """
        Run a full reconciliation pass.

        This is the main entry point for scheduled reconciliation runs.
        It acquires a global lock to prevent concurrent runs, then checks
        both PaymentOrders and Payouts for discrepancies.

        Args:
            lookback_hours: How far back to check (default: 24)
            stuck_threshold_hours: Consider records stuck after this (default: 2)
            max_records: Maximum records per entity type to check (default: 500)

        Returns:
            ServiceResult containing ReconciliationRunResult with summary stats

        Raises:
            ReconciliationLockError: If another run is already in progress
        """
        cls.get_logger().info(
            "Starting reconciliation run",
            extra={
                "lookback_hours": lookback_hours,
                "stuck_threshold_hours": stuck_threshold_hours,
                "max_records": max_records,
            },
        )

        # Acquire global lock to prevent concurrent runs
        lock_key = "reconciliation:run"
        try:
            lock = DistributedLock(
                lock_key,
                ttl=RECONCILIATION_RUN_LOCK_TTL,
                timeout=RECONCILIATION_RUN_LOCK_TIMEOUT,
            )
            lock.acquire()
        except LockAcquisitionError:
            cls.get_logger().warning(
                "Another reconciliation run is in progress",
                extra={"lock_key": lock_key},
            )
            raise ReconciliationLockError(
                "Another reconciliation run is in progress",
                details={"lock_key": lock_key},
            )

        try:
            return cls._run_reconciliation_with_lock(
                lookback_hours=lookback_hours,
                stuck_threshold_hours=stuck_threshold_hours,
                max_records=max_records,
            )
        finally:
            lock.release()

    @classmethod
    def reconcile_payment_order(
        cls,
        payment_order_id: uuid.UUID,
    ) -> ServiceResult[HealingResult | None]:
        """
        Reconcile a single PaymentOrder against Stripe.

        Use this for on-demand reconciliation of a specific payment.

        Args:
            payment_order_id: UUID of the PaymentOrder to reconcile

        Returns:
            ServiceResult containing HealingResult if discrepancy found, None otherwise
        """
        cls.get_logger().info(
            "Reconciling single payment order",
            extra={"payment_order_id": str(payment_order_id)},
        )

        try:
            order = PaymentOrder.objects.get(id=payment_order_id)
        except PaymentOrder.DoesNotExist:
            return ServiceResult.failure(
                f"PaymentOrder {payment_order_id} not found",
                error_code="PAYMENT_ORDER_NOT_FOUND",
            )

        discrepancy = cls._check_payment_order(order, stuck_threshold_hours=2)
        if not discrepancy:
            return ServiceResult.success(None)

        result = cls._heal_discrepancy(discrepancy, run_id=None)
        return ServiceResult.success(result)

    @classmethod
    def reconcile_payout(
        cls,
        payout_id: uuid.UUID,
    ) -> ServiceResult[HealingResult | None]:
        """
        Reconcile a single Payout against Stripe.

        Use this for on-demand reconciliation of a specific payout.

        Args:
            payout_id: UUID of the Payout to reconcile

        Returns:
            ServiceResult containing HealingResult if discrepancy found, None otherwise
        """
        cls.get_logger().info(
            "Reconciling single payout",
            extra={"payout_id": str(payout_id)},
        )

        try:
            payout = Payout.objects.select_related(
                "payment_order",
                "connected_account",
            ).get(id=payout_id)
        except Payout.DoesNotExist:
            return ServiceResult.failure(
                f"Payout {payout_id} not found",
                error_code="PAYOUT_NOT_FOUND",
            )

        discrepancy = cls._check_payout(payout, stuck_threshold_hours=2)
        if not discrepancy:
            return ServiceResult.success(None)

        result = cls._heal_discrepancy(discrepancy, run_id=None)
        return ServiceResult.success(result)

    # =========================================================================
    # Internal: Run Orchestration
    # =========================================================================

    @classmethod
    def _run_reconciliation_with_lock(
        cls,
        lookback_hours: int,
        stuck_threshold_hours: int,
        max_records: int,
    ) -> ServiceResult[ReconciliationRunResult]:
        """Execute reconciliation run with lock already held."""
        started_at = timezone.now()

        # Create run record
        run = ReconciliationRun.objects.create(
            started_at=started_at,
            lookback_hours=lookback_hours,
            stuck_threshold_hours=stuck_threshold_hours,
            status=ReconciliationRunStatus.RUNNING,
        )

        results: list[HealingResult] = []
        payment_orders_checked = 0
        payouts_checked = 0

        try:
            # Phase 1: Reconcile PaymentOrders
            cls.get_logger().info(
                "Phase 1: Reconciling payment orders",
                extra={"run_id": str(run.id)},
            )

            payment_results, payment_orders_checked = cls._reconcile_payment_orders(
                run_id=run.id,
                lookback_hours=lookback_hours,
                stuck_threshold_hours=stuck_threshold_hours,
                limit=max_records,
            )
            results.extend(payment_results)

            # Phase 2: Reconcile Payouts
            cls.get_logger().info(
                "Phase 2: Reconciling payouts",
                extra={"run_id": str(run.id)},
            )

            payout_results, payouts_checked = cls._reconcile_payouts(
                run_id=run.id,
                lookback_hours=lookback_hours,
                stuck_threshold_hours=stuck_threshold_hours,
                limit=max_records,
            )
            results.extend(payout_results)

            # Calculate summary stats
            discrepancies_found = len(results)
            auto_healed = sum(
                1 for r in results if r.resolution == DiscrepancyResolution.AUTO_HEALED
            )
            flagged_for_review = sum(
                1
                for r in results
                if r.resolution == DiscrepancyResolution.FLAGGED_FOR_REVIEW
            )
            failed_to_heal = sum(
                1
                for r in results
                if r.resolution == DiscrepancyResolution.FAILED_TO_HEAL
            )

            # Update run record
            completed_at = timezone.now()
            run.completed_at = completed_at
            run.payment_orders_checked = payment_orders_checked
            run.payouts_checked = payouts_checked
            run.discrepancies_found = discrepancies_found
            run.auto_healed = auto_healed
            run.flagged_for_review = flagged_for_review
            run.failed_to_heal = failed_to_heal
            run.status = ReconciliationRunStatus.COMPLETED
            run.save()

            cls.get_logger().info(
                "Reconciliation run completed",
                extra={
                    "run_id": str(run.id),
                    "payment_orders_checked": payment_orders_checked,
                    "payouts_checked": payouts_checked,
                    "discrepancies_found": discrepancies_found,
                    "auto_healed": auto_healed,
                    "flagged_for_review": flagged_for_review,
                    "failed_to_heal": failed_to_heal,
                    "duration_seconds": (completed_at - started_at).total_seconds(),
                },
            )

            return ServiceResult.success(
                ReconciliationRunResult(
                    run_id=run.id,
                    started_at=started_at,
                    completed_at=completed_at,
                    payment_orders_checked=payment_orders_checked,
                    payouts_checked=payouts_checked,
                    discrepancies_found=discrepancies_found,
                    auto_healed=auto_healed,
                    flagged_for_review=flagged_for_review,
                    failed_to_heal=failed_to_heal,
                    results=results,
                )
            )

        except Exception as e:
            # Mark run as failed
            run.completed_at = timezone.now()
            run.status = ReconciliationRunStatus.FAILED
            run.error_message = str(e)
            run.save()

            cls.get_logger().error(
                "Reconciliation run failed",
                extra={
                    "run_id": str(run.id),
                    "error": str(e),
                },
                exc_info=True,
            )

            raise ReconciliationError(
                f"Reconciliation run failed: {e}",
                details={"run_id": str(run.id)},
            )

    # =========================================================================
    # Internal: PaymentOrder Reconciliation
    # =========================================================================

    @classmethod
    def _reconcile_payment_orders(
        cls,
        run_id: uuid.UUID,
        lookback_hours: int,
        stuck_threshold_hours: int,
        limit: int,
    ) -> tuple[list[HealingResult], int]:
        """
        Reconcile PaymentOrders within the lookback window.

        Returns tuple of (healing_results, total_checked).
        """
        cutoff = timezone.now() - timedelta(hours=lookback_hours)

        # Get PaymentOrders in reconcilable states
        orders = PaymentOrder.objects.filter(
            state__in=[
                PaymentOrderState.PENDING,
                PaymentOrderState.PROCESSING,
            ],
            created_at__gte=cutoff,
        ).order_by("created_at")[:limit]

        results: list[HealingResult] = []
        total_checked = 0

        for order in orders:
            total_checked += 1

            try:
                discrepancy = cls._check_payment_order(order, stuck_threshold_hours)
                if discrepancy:
                    result = cls._heal_discrepancy(discrepancy, run_id)
                    results.append(result)

                    # Persist discrepancy record
                    cls._record_discrepancy(run_id, discrepancy, result)

            except (StripeRateLimitError, StripeAPIUnavailableError) as e:
                cls.get_logger().warning(
                    "Stripe API error during payment order reconciliation, skipping",
                    extra={
                        "payment_order_id": str(order.id),
                        "error": str(e),
                    },
                )
                # Continue with other records
                continue

            except Exception as e:
                cls.get_logger().error(
                    "Error reconciling payment order",
                    extra={
                        "payment_order_id": str(order.id),
                        "error": str(e),
                    },
                    exc_info=True,
                )
                # Continue with other records
                continue

        return results, total_checked

    @classmethod
    def _check_payment_order(
        cls,
        order: PaymentOrder,
        stuck_threshold_hours: int,
    ) -> Discrepancy | None:
        """
        Check a single PaymentOrder for discrepancies.

        Returns Discrepancy if found, None otherwise.
        """
        # Skip if no Stripe PaymentIntent ID
        if not order.stripe_payment_intent_id:
            return None

        # Fetch current state from Stripe
        adapter = cls.get_stripe_adapter()
        try:
            pi_result = adapter.retrieve_payment_intent(
                order.stripe_payment_intent_id,
                trace_id=f"reconciliation:{order.id}",
            )
        except Exception as e:
            cls.get_logger().warning(
                "Failed to retrieve PaymentIntent from Stripe",
                extra={
                    "payment_order_id": str(order.id),
                    "stripe_payment_intent_id": order.stripe_payment_intent_id,
                    "error": str(e),
                },
            )
            raise

        stripe_status = pi_result.raw_response.get("status")

        # Check for discrepancies based on local vs Stripe state
        if order.state == PaymentOrderState.PROCESSING:
            if stripe_status == "succeeded":
                return Discrepancy(
                    discrepancy_type=DiscrepancyType.STRIPE_SUCCEEDED_LOCAL_PROCESSING,
                    entity_type="payment_order",
                    entity_id=order.id,
                    stripe_id=order.stripe_payment_intent_id,
                    local_state=order.state,
                    stripe_state=stripe_status,
                    details={
                        "captured_amount": pi_result.raw_response.get(
                            "amount_received", 0
                        ),
                    },
                )

            if stripe_status == "canceled":
                return Discrepancy(
                    discrepancy_type=DiscrepancyType.STRIPE_CANCELED_LOCAL_ACTIVE,
                    entity_type="payment_order",
                    entity_id=order.id,
                    stripe_id=order.stripe_payment_intent_id,
                    local_state=order.state,
                    stripe_state=stripe_status,
                    details={
                        "cancellation_reason": pi_result.raw_response.get(
                            "cancellation_reason"
                        ),
                    },
                )

            # Check if stuck in PROCESSING
            stuck_threshold = timezone.now() - timedelta(hours=stuck_threshold_hours)
            if order.updated_at < stuck_threshold:
                return Discrepancy(
                    discrepancy_type=DiscrepancyType.PAYMENT_STUCK_IN_PROCESSING,
                    entity_type="payment_order",
                    entity_id=order.id,
                    stripe_id=order.stripe_payment_intent_id,
                    local_state=order.state,
                    stripe_state=stripe_status,
                    details={
                        "stuck_since": order.updated_at.isoformat(),
                        "hours_stuck": (
                            timezone.now() - order.updated_at
                        ).total_seconds()
                        / 3600,
                    },
                )

        elif order.state == PaymentOrderState.PENDING:
            if stripe_status == "succeeded":
                return Discrepancy(
                    discrepancy_type=DiscrepancyType.STRIPE_SUCCEEDED_LOCAL_PENDING,
                    entity_type="payment_order",
                    entity_id=order.id,
                    stripe_id=order.stripe_payment_intent_id,
                    local_state=order.state,
                    stripe_state=stripe_status,
                    details={
                        "captured_amount": pi_result.raw_response.get(
                            "amount_received", 0
                        ),
                    },
                )

        return None

    # =========================================================================
    # Internal: Payout Reconciliation
    # =========================================================================

    @classmethod
    def _reconcile_payouts(
        cls,
        run_id: uuid.UUID,
        lookback_hours: int,
        stuck_threshold_hours: int,
        limit: int,
    ) -> tuple[list[HealingResult], int]:
        """
        Reconcile Payouts within the lookback window.

        Returns tuple of (healing_results, total_checked).
        """
        cutoff = timezone.now() - timedelta(hours=lookback_hours)

        # Get Payouts in reconcilable states
        payouts = (
            Payout.objects.filter(
                state__in=[
                    PayoutState.PROCESSING,
                    PayoutState.SCHEDULED,
                ],
                created_at__gte=cutoff,
            )
            .select_related("payment_order", "connected_account")
            .order_by("created_at")[:limit]
        )

        results: list[HealingResult] = []
        total_checked = 0

        for payout in payouts:
            total_checked += 1

            try:
                discrepancy = cls._check_payout(payout, stuck_threshold_hours)
                if discrepancy:
                    result = cls._heal_discrepancy(discrepancy, run_id)
                    results.append(result)

                    # Persist discrepancy record
                    cls._record_discrepancy(run_id, discrepancy, result)

            except (StripeRateLimitError, StripeAPIUnavailableError) as e:
                cls.get_logger().warning(
                    "Stripe API error during payout reconciliation, skipping",
                    extra={
                        "payout_id": str(payout.id),
                        "error": str(e),
                    },
                )
                continue

            except Exception as e:
                cls.get_logger().error(
                    "Error reconciling payout",
                    extra={
                        "payout_id": str(payout.id),
                        "error": str(e),
                    },
                    exc_info=True,
                )
                continue

        return results, total_checked

    @classmethod
    def _check_payout(
        cls,
        payout: Payout,
        stuck_threshold_hours: int,
    ) -> Discrepancy | None:
        """
        Check a single Payout for discrepancies.

        This is the critical check for Phase 3 failures where Stripe transfer
        succeeded but the stripe_transfer_id wasn't saved.

        Returns Discrepancy if found, None otherwise.
        """
        adapter = cls.get_stripe_adapter()

        # Case 1: Payout in PROCESSING with no stripe_transfer_id
        # This is the Phase 3 failure scenario - need to search Stripe
        if payout.state == PayoutState.PROCESSING and not payout.stripe_transfer_id:
            # Search for transfer by metadata match
            transfer = cls._find_transfer_by_metadata(payout)
            if transfer:
                return Discrepancy(
                    discrepancy_type=DiscrepancyType.STRIPE_TRANSFER_EXISTS_LOCAL_PROCESSING_NO_ID,
                    entity_type="payout",
                    entity_id=payout.id,
                    stripe_id=transfer.id,
                    local_state=payout.state,
                    stripe_state=transfer.raw_response.get("status", "unknown"),
                    details={
                        "transfer_id": transfer.id,
                        "transfer_status": transfer.raw_response.get("status"),
                        "payment_order_id": str(payout.payment_order_id),
                    },
                )

            # Check if stuck without transfer
            stuck_threshold = timezone.now() - timedelta(hours=stuck_threshold_hours)
            if payout.updated_at < stuck_threshold:
                return Discrepancy(
                    discrepancy_type=DiscrepancyType.PAYOUT_STUCK_IN_PROCESSING,
                    entity_type="payout",
                    entity_id=payout.id,
                    stripe_id=None,
                    local_state=payout.state,
                    stripe_state=None,
                    details={
                        "stuck_since": payout.updated_at.isoformat(),
                        "hours_stuck": (
                            timezone.now() - payout.updated_at
                        ).total_seconds()
                        / 3600,
                        "no_transfer_id": True,
                    },
                )

            return None

        # Case 2: Payout has stripe_transfer_id - verify against Stripe
        if payout.stripe_transfer_id:
            try:
                transfer_result = adapter.retrieve_transfer(
                    payout.stripe_transfer_id,
                    trace_id=f"reconciliation:{payout.id}",
                )
            except Exception as e:
                cls.get_logger().warning(
                    "Failed to retrieve Transfer from Stripe",
                    extra={
                        "payout_id": str(payout.id),
                        "stripe_transfer_id": payout.stripe_transfer_id,
                        "error": str(e),
                    },
                )
                raise

            stripe_status = transfer_result.raw_response.get("status")

            # Check for state discrepancies
            if payout.state == PayoutState.PROCESSING:
                if stripe_status == "paid":
                    return Discrepancy(
                        discrepancy_type=DiscrepancyType.STRIPE_TRANSFER_PAID_LOCAL_PROCESSING,
                        entity_type="payout",
                        entity_id=payout.id,
                        stripe_id=payout.stripe_transfer_id,
                        local_state=payout.state,
                        stripe_state=stripe_status,
                        details={
                            "amount": transfer_result.raw_response.get("amount"),
                        },
                    )

                if stripe_status == "failed":
                    return Discrepancy(
                        discrepancy_type=DiscrepancyType.STRIPE_TRANSFER_FAILED_LOCAL_PROCESSING,
                        entity_type="payout",
                        entity_id=payout.id,
                        stripe_id=payout.stripe_transfer_id,
                        local_state=payout.state,
                        stripe_state=stripe_status,
                        details={
                            "failure_reason": transfer_result.raw_response.get(
                                "failure_message"
                            ),
                        },
                    )

            elif payout.state == PayoutState.SCHEDULED:
                if stripe_status == "paid":
                    return Discrepancy(
                        discrepancy_type=DiscrepancyType.STRIPE_TRANSFER_PAID_LOCAL_SCHEDULED,
                        entity_type="payout",
                        entity_id=payout.id,
                        stripe_id=payout.stripe_transfer_id,
                        local_state=payout.state,
                        stripe_state=stripe_status,
                        details={
                            "amount": transfer_result.raw_response.get("amount"),
                        },
                    )

        return None

    @classmethod
    def _find_transfer_by_metadata(cls, payout: Payout):
        """
        Search for a Stripe Transfer by payout_id in metadata.

        This handles the Phase 3 failure scenario where the Stripe call
        succeeded but the transfer_id wasn't saved to our database.

        Returns TransferResult if found, None otherwise.
        """
        adapter = cls.get_stripe_adapter()

        # Look for recent transfers
        lookback = timezone.now() - timedelta(hours=48)

        try:
            transfers = adapter.list_recent_transfers(
                created_after=lookback,
                limit=100,
                trace_id=f"reconciliation:metadata_search:{payout.id}",
            )
        except Exception as e:
            cls.get_logger().warning(
                "Failed to list transfers from Stripe",
                extra={
                    "payout_id": str(payout.id),
                    "error": str(e),
                },
            )
            return None

        # Search for matching payout_id in metadata
        payout_id_str = str(payout.id)
        for transfer in transfers:
            metadata = transfer.raw_response.get("metadata", {})
            if metadata.get("payout_id") == payout_id_str:
                cls.get_logger().info(
                    "Found matching transfer by metadata",
                    extra={
                        "payout_id": str(payout.id),
                        "transfer_id": transfer.id,
                    },
                )
                return transfer

        return None

    # =========================================================================
    # Internal: Healing Logic
    # =========================================================================

    @classmethod
    def _heal_discrepancy(
        cls,
        discrepancy: Discrepancy,
        run_id: uuid.UUID | None,
    ) -> HealingResult:
        """
        Attempt to heal a detected discrepancy.

        Uses distributed lock to prevent races during healing.
        Re-verifies discrepancy after acquiring lock (webhook may have fixed it).
        """
        lock_key = (
            f"reconciliation:heal:{discrepancy.entity_type}:{discrepancy.entity_id}"
        )

        try:
            with DistributedLock(
                lock_key, ttl=HEAL_LOCK_TTL, timeout=HEAL_LOCK_TIMEOUT
            ):
                return cls._heal_discrepancy_with_lock(discrepancy, run_id)
        except LockAcquisitionError:
            cls.get_logger().info(
                "Could not acquire heal lock, skipping",
                extra={
                    "entity_type": discrepancy.entity_type,
                    "entity_id": str(discrepancy.entity_id),
                },
            )
            return HealingResult(
                discrepancy=discrepancy,
                resolution=DiscrepancyResolution.FLAGGED_FOR_REVIEW,
                action_taken=None,
                error="Could not acquire lock - another process may be healing",
            )

    @classmethod
    def _heal_discrepancy_with_lock(
        cls,
        discrepancy: Discrepancy,
        run_id: uuid.UUID | None,
    ) -> HealingResult:
        """Execute healing with lock already held."""
        cls.get_logger().info(
            "Attempting to heal discrepancy",
            extra={
                "discrepancy_type": discrepancy.discrepancy_type.value,
                "entity_type": discrepancy.entity_type,
                "entity_id": str(discrepancy.entity_id),
                "run_id": str(run_id) if run_id else None,
            },
        )

        # Route to appropriate healer based on discrepancy type
        healers = {
            # PaymentOrder healers
            DiscrepancyType.STRIPE_SUCCEEDED_LOCAL_PROCESSING: cls._heal_payment_succeeded,
            DiscrepancyType.STRIPE_SUCCEEDED_LOCAL_PENDING: cls._heal_payment_succeeded_from_pending,
            DiscrepancyType.STRIPE_FAILED_LOCAL_PROCESSING: cls._heal_payment_failed,
            DiscrepancyType.STRIPE_CANCELED_LOCAL_ACTIVE: cls._flag_for_review,
            DiscrepancyType.PAYMENT_STUCK_IN_PROCESSING: cls._flag_for_review,
            # Payout healers
            DiscrepancyType.STRIPE_TRANSFER_EXISTS_LOCAL_PROCESSING_NO_ID: cls._heal_payout_backfill_transfer_id,
            DiscrepancyType.STRIPE_TRANSFER_PAID_LOCAL_PROCESSING: cls._heal_payout_complete,
            DiscrepancyType.STRIPE_TRANSFER_PAID_LOCAL_SCHEDULED: cls._heal_payout_complete,
            DiscrepancyType.STRIPE_TRANSFER_FAILED_LOCAL_PROCESSING: cls._flag_for_review,
            DiscrepancyType.PAYOUT_STUCK_IN_PROCESSING: cls._flag_for_review,
        }

        healer = healers.get(discrepancy.discrepancy_type, cls._flag_for_review)

        try:
            return healer(discrepancy, run_id)
        except Exception as e:
            cls.get_logger().error(
                "Healing failed",
                extra={
                    "discrepancy_type": discrepancy.discrepancy_type.value,
                    "entity_type": discrepancy.entity_type,
                    "entity_id": str(discrepancy.entity_id),
                    "error": str(e),
                },
                exc_info=True,
            )
            return HealingResult(
                discrepancy=discrepancy,
                resolution=DiscrepancyResolution.FAILED_TO_HEAL,
                error=str(e),
            )

    @classmethod
    def _heal_payment_succeeded(
        cls,
        discrepancy: Discrepancy,
        run_id: uuid.UUID | None,
    ) -> HealingResult:
        """
        Heal PaymentOrder where Stripe succeeded but we show PROCESSING.

        Transitions: PROCESSING -> CAPTURED
        """
        with transaction.atomic():
            order = PaymentOrder.objects.select_for_update().get(
                id=discrepancy.entity_id
            )

            # Re-verify discrepancy still exists
            if order.state != PaymentOrderState.PROCESSING:
                cls.get_logger().info(
                    "Payment order state already changed, skipping heal",
                    extra={
                        "payment_order_id": str(order.id),
                        "current_state": order.state,
                    },
                )
                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.AUTO_HEALED,
                    action_taken="State already transitioned (likely by webhook)",
                )

            try:
                order.capture()
                order.save()

                cls.get_logger().info(
                    "Healed payment order: PROCESSING -> CAPTURED",
                    extra={
                        "payment_order_id": str(order.id),
                        "run_id": str(run_id) if run_id else None,
                    },
                )

                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.AUTO_HEALED,
                    action_taken="Transitioned payment order from PROCESSING to CAPTURED",
                )

            except TransitionNotAllowed as e:
                raise HealingError(
                    f"State transition not allowed: {e}",
                    details={
                        "payment_order_id": str(order.id),
                        "current_state": order.state,
                    },
                )

    @classmethod
    def _heal_payment_succeeded_from_pending(
        cls,
        discrepancy: Discrepancy,
        run_id: uuid.UUID | None,
    ) -> HealingResult:
        """
        Heal PaymentOrder where Stripe succeeded but we show PENDING.

        This is a more severe discrepancy - we missed both PROCESSING and CAPTURED.
        Transitions: PENDING -> PROCESSING -> CAPTURED
        """
        with transaction.atomic():
            order = PaymentOrder.objects.select_for_update().get(
                id=discrepancy.entity_id
            )

            # Re-verify discrepancy still exists
            if order.state != PaymentOrderState.PENDING:
                cls.get_logger().info(
                    "Payment order state already changed, skipping heal",
                    extra={
                        "payment_order_id": str(order.id),
                        "current_state": order.state,
                    },
                )
                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.AUTO_HEALED,
                    action_taken="State already transitioned (likely by webhook)",
                )

            try:
                # Two transitions needed
                order.process()
                order.save()
                order.refresh_from_db()

                order.capture()
                order.save()

                cls.get_logger().info(
                    "Healed payment order: PENDING -> PROCESSING -> CAPTURED",
                    extra={
                        "payment_order_id": str(order.id),
                        "run_id": str(run_id) if run_id else None,
                    },
                )

                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.AUTO_HEALED,
                    action_taken="Transitioned payment order from PENDING to CAPTURED (via PROCESSING)",
                )

            except TransitionNotAllowed as e:
                raise HealingError(
                    f"State transition not allowed: {e}",
                    details={
                        "payment_order_id": str(order.id),
                        "current_state": order.state,
                    },
                )

    @classmethod
    def _heal_payment_failed(
        cls,
        discrepancy: Discrepancy,
        run_id: uuid.UUID | None,
    ) -> HealingResult:
        """
        Heal PaymentOrder where Stripe failed but we show PROCESSING.

        Transitions: PROCESSING -> FAILED
        """
        with transaction.atomic():
            order = PaymentOrder.objects.select_for_update().get(
                id=discrepancy.entity_id
            )

            if order.state != PaymentOrderState.PROCESSING:
                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.AUTO_HEALED,
                    action_taken="State already transitioned",
                )

            try:
                order.fail(reason="Reconciliation: Stripe PaymentIntent failed")
                order.save()

                cls.get_logger().info(
                    "Healed payment order: PROCESSING -> FAILED",
                    extra={
                        "payment_order_id": str(order.id),
                        "run_id": str(run_id) if run_id else None,
                    },
                )

                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.AUTO_HEALED,
                    action_taken="Transitioned payment order from PROCESSING to FAILED",
                )

            except TransitionNotAllowed as e:
                raise HealingError(
                    f"State transition not allowed: {e}",
                    details={
                        "payment_order_id": str(order.id),
                        "current_state": order.state,
                    },
                )

    @classmethod
    def _heal_payout_backfill_transfer_id(
        cls,
        discrepancy: Discrepancy,
        run_id: uuid.UUID | None,
    ) -> HealingResult:
        """
        Heal Payout in PROCESSING with no transfer_id but Stripe has it.

        This is the Phase 3 failure recovery - backfill the transfer_id
        and optionally advance state if transfer is already paid.
        """
        transfer_id = discrepancy.stripe_id
        transfer_status = discrepancy.details.get("transfer_status")

        with transaction.atomic():
            payout = Payout.objects.select_for_update().get(id=discrepancy.entity_id)

            if payout.stripe_transfer_id:
                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.AUTO_HEALED,
                    action_taken="Transfer ID already backfilled",
                )

            # Backfill the transfer ID
            payout.stripe_transfer_id = transfer_id
            action = f"Backfilled stripe_transfer_id: {transfer_id}"

            # If transfer is already paid, complete the payout
            if transfer_status == "paid" and payout.state == PayoutState.PROCESSING:
                try:
                    payout.complete()
                    action += "; Transitioned to PAID"
                except TransitionNotAllowed:
                    pass  # OK if transition not allowed from current state

            payout.save()

            cls.get_logger().info(
                "Healed payout by backfilling transfer_id",
                extra={
                    "payout_id": str(payout.id),
                    "transfer_id": transfer_id,
                    "transfer_status": transfer_status,
                    "run_id": str(run_id) if run_id else None,
                },
            )

            return HealingResult(
                discrepancy=discrepancy,
                resolution=DiscrepancyResolution.AUTO_HEALED,
                action_taken=action,
            )

    @classmethod
    def _heal_payout_complete(
        cls,
        discrepancy: Discrepancy,
        run_id: uuid.UUID | None,
    ) -> HealingResult:
        """
        Heal Payout where Stripe transfer is paid but we show PROCESSING/SCHEDULED.

        Transitions: PROCESSING/SCHEDULED -> PAID
        """
        with transaction.atomic():
            payout = Payout.objects.select_for_update().get(id=discrepancy.entity_id)

            if payout.state == PayoutState.PAID:
                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.AUTO_HEALED,
                    action_taken="Payout already in PAID state",
                )

            if payout.state not in [PayoutState.PROCESSING, PayoutState.SCHEDULED]:
                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.FLAGGED_FOR_REVIEW,
                    action_taken=None,
                    error=f"Payout in unexpected state: {payout.state}",
                )

            try:
                payout.complete()
                payout.save()

                cls.get_logger().info(
                    f"Healed payout: {discrepancy.local_state} -> PAID",
                    extra={
                        "payout_id": str(payout.id),
                        "run_id": str(run_id) if run_id else None,
                    },
                )

                return HealingResult(
                    discrepancy=discrepancy,
                    resolution=DiscrepancyResolution.AUTO_HEALED,
                    action_taken=f"Transitioned payout from {discrepancy.local_state} to PAID",
                )

            except TransitionNotAllowed as e:
                raise HealingError(
                    f"State transition not allowed: {e}",
                    details={
                        "payout_id": str(payout.id),
                        "current_state": payout.state,
                    },
                )

    @classmethod
    def _flag_for_review(
        cls,
        discrepancy: Discrepancy,
        run_id: uuid.UUID | None,
    ) -> HealingResult:
        """
        Flag a discrepancy for manual review.

        Used for ambiguous cases that require human judgment.
        """
        cls.get_logger().warning(
            "Discrepancy flagged for review",
            extra={
                "discrepancy_type": discrepancy.discrepancy_type.value,
                "entity_type": discrepancy.entity_type,
                "entity_id": str(discrepancy.entity_id),
                "stripe_id": discrepancy.stripe_id,
                "local_state": discrepancy.local_state,
                "stripe_state": discrepancy.stripe_state,
                "run_id": str(run_id) if run_id else None,
            },
        )

        return HealingResult(
            discrepancy=discrepancy,
            resolution=DiscrepancyResolution.FLAGGED_FOR_REVIEW,
            action_taken="Flagged for manual review",
        )

    # =========================================================================
    # Internal: Persistence
    # =========================================================================

    @classmethod
    def _record_discrepancy(
        cls,
        run_id: uuid.UUID | None,
        discrepancy: Discrepancy,
        result: HealingResult,
    ) -> None:
        """Persist discrepancy record to database for audit and review."""
        if run_id is None:
            # Single-entity reconciliation without a run
            return

        try:
            run = ReconciliationRun.objects.get(id=run_id)

            ReconciliationDiscrepancy.objects.create(
                run=run,
                entity_type=discrepancy.entity_type,
                entity_id=discrepancy.entity_id,
                stripe_id=discrepancy.stripe_id or "",
                discrepancy_type=discrepancy.discrepancy_type.value,
                local_state=discrepancy.local_state,
                stripe_state=discrepancy.stripe_state or "",
                details=discrepancy.details,
                resolution=result.resolution,
                action_taken=result.action_taken or "",
                error_message=result.error or "",
                ledger_entry_id=result.ledger_entry_id,
            )

        except Exception as e:
            cls.get_logger().error(
                "Failed to record discrepancy",
                extra={
                    "run_id": str(run_id),
                    "discrepancy_type": discrepancy.discrepancy_type.value,
                    "error": str(e),
                },
                exc_info=True,
            )


__all__ = [
    "ReconciliationService",
    "ReconciliationRunResult",
    "HealingResult",
    "Discrepancy",
    "DiscrepancyType",
]
