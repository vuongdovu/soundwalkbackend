"""
Reconciliation models for tracking reconciliation runs and discrepancies.

This module provides models for persisting reconciliation history:
- ReconciliationRun: Tracks each execution of the reconciliation process
- ReconciliationDiscrepancy: Records individual discrepancies found and their resolution

These models enable:
1. Historical audit trail of reconciliation activity
2. Review queue for flagged discrepancies requiring manual intervention
3. Metrics and reporting on reconciliation health
4. Debugging and incident investigation

Usage:
    from payments.models import ReconciliationRun, ReconciliationDiscrepancy

    # Start a reconciliation run
    run = ReconciliationRun.objects.create(
        lookback_hours=24,
        stuck_threshold_hours=2,
        started_at=timezone.now(),
    )

    # Record a discrepancy
    discrepancy = ReconciliationDiscrepancy.objects.create(
        run=run,
        entity_type='payout',
        entity_id=payout.id,
        stripe_id='tr_xxx',
        discrepancy_type='stripe_transfer_paid_local_processing',
        local_state='processing',
        stripe_state='paid',
        resolution='auto_healed',
        action_taken='Transitioned payout to PAID state',
    )
"""

from __future__ import annotations

from django.db import models

from core.model_mixins import UUIDPrimaryKeyMixin
from core.models import BaseModel


class ReconciliationRunStatus(models.TextChoices):
    """Status of a reconciliation run."""

    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class DiscrepancyResolution(models.TextChoices):
    """How a discrepancy was resolved."""

    AUTO_HEALED = "auto_healed", "Auto Healed"
    FLAGGED_FOR_REVIEW = "flagged_for_review", "Flagged for Review"
    MANUALLY_RESOLVED = "manually_resolved", "Manually Resolved"
    FAILED_TO_HEAL = "failed_to_heal", "Failed to Heal"


class ReconciliationRun(UUIDPrimaryKeyMixin, BaseModel):
    """
    Tracks a reconciliation run execution.

    Each run of the reconciliation service creates a ReconciliationRun record
    to provide:
    - Historical record of when reconciliation ran
    - Configuration used (lookback, thresholds)
    - Summary statistics of what was found and fixed
    - Status tracking for monitoring

    The reconciliation service creates a run at the start, updates statistics
    as it processes records, and marks it complete/failed at the end.

    Indexes:
        - (status, started_at): For finding recent runs by status
        - (started_at): For time-based queries

    Example:
        run = ReconciliationRun.objects.create(
            lookback_hours=24,
            stuck_threshold_hours=2,
            started_at=timezone.now(),
        )

        # ... reconciliation processing ...

        run.discrepancies_found = 5
        run.auto_healed = 3
        run.flagged_for_review = 2
        run.status = ReconciliationRunStatus.COMPLETED
        run.completed_at = timezone.now()
        run.save()
    """

    # When the run executed
    started_at = models.DateTimeField(
        help_text="When this reconciliation run started",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this reconciliation run completed (or failed)",
    )

    # Configuration used for this run
    lookback_hours = models.PositiveIntegerField(
        help_text="How many hours back this run checked",
    )
    stuck_threshold_hours = models.PositiveIntegerField(
        help_text="Threshold for considering records 'stuck'",
    )

    # Results summary
    payment_orders_checked = models.PositiveIntegerField(
        default=0,
        help_text="Number of PaymentOrder records checked",
    )
    payouts_checked = models.PositiveIntegerField(
        default=0,
        help_text="Number of Payout records checked",
    )
    discrepancies_found = models.PositiveIntegerField(
        default=0,
        help_text="Total discrepancies found",
    )
    auto_healed = models.PositiveIntegerField(
        default=0,
        help_text="Discrepancies automatically healed",
    )
    flagged_for_review = models.PositiveIntegerField(
        default=0,
        help_text="Discrepancies requiring manual review",
    )
    failed_to_heal = models.PositiveIntegerField(
        default=0,
        help_text="Discrepancies that failed to heal",
    )

    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=ReconciliationRunStatus.choices,
        default=ReconciliationRunStatus.RUNNING,
        db_index=True,
        help_text="Current status of this reconciliation run",
    )
    error_message = models.TextField(
        blank=True,
        help_text="Error message if the run failed",
    )

    class Meta:
        indexes = [
            models.Index(fields=["status", "started_at"]),
            models.Index(fields=["started_at"]),
        ]
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"ReconciliationRun({self.id}, {self.status}, {self.started_at})"

    @property
    def duration_seconds(self) -> float | None:
        """Calculate run duration in seconds, or None if not complete."""
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class ReconciliationDiscrepancy(UUIDPrimaryKeyMixin, BaseModel):
    """
    Tracks individual discrepancies found during reconciliation.

    Each discrepancy represents a mismatch between local state and Stripe's
    source of truth. This model provides:
    - A review queue for operators to investigate flagged issues
    - Audit trail of what was found and how it was resolved
    - Data for analyzing patterns in discrepancies

    Discrepancies may be:
    - Auto-healed: The service automatically corrected the state
    - Flagged for review: Requires human judgment to resolve
    - Manually resolved: A human reviewed and resolved the issue
    - Failed to heal: Automatic healing was attempted but failed

    Indexes:
        - (resolution, reviewed): For finding unreviewed flagged items
        - (entity_type, entity_id): For finding discrepancies by entity
        - (discrepancy_type): For analyzing discrepancy patterns
        - (run, resolution): For run-level statistics

    Example:
        discrepancy = ReconciliationDiscrepancy.objects.create(
            run=run,
            entity_type='payout',
            entity_id=payout.id,
            stripe_id='tr_xxx',
            discrepancy_type='stripe_transfer_paid_local_processing',
            local_state='processing',
            stripe_state='paid',
            resolution='auto_healed',
            action_taken='Transitioned payout to PAID state',
        )
    """

    # Link to the run that found this discrepancy
    run = models.ForeignKey(
        ReconciliationRun,
        on_delete=models.CASCADE,
        related_name="discrepancies",
        help_text="The reconciliation run that found this discrepancy",
    )

    # What was affected
    entity_type = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Type of entity (payment_order or payout)",
    )
    entity_id = models.UUIDField(
        help_text="ID of the affected entity",
    )
    stripe_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Related Stripe object ID (pi_xxx, tr_xxx)",
    )

    # Discrepancy details
    discrepancy_type = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Type of discrepancy detected (e.g., stripe_transfer_paid_local_processing)",
    )
    local_state = models.CharField(
        max_length=50,
        help_text="State of the local record when discrepancy was detected",
    )
    stripe_state = models.CharField(
        max_length=50,
        blank=True,
        help_text="State/status reported by Stripe",
    )
    details = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional context about the discrepancy",
    )

    # Resolution
    resolution = models.CharField(
        max_length=20,
        choices=DiscrepancyResolution.choices,
        db_index=True,
        help_text="How this discrepancy was resolved",
    )
    action_taken = models.TextField(
        blank=True,
        help_text="Description of what action was taken to resolve",
    )
    error_message = models.TextField(
        blank=True,
        help_text="Error message if healing failed",
    )

    # Review tracking (for flagged discrepancies)
    reviewed = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether a human has reviewed this discrepancy",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this discrepancy was reviewed",
    )
    reviewed_by = models.ForeignKey(
        "authentication.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_discrepancies",
        help_text="User who reviewed this discrepancy",
    )
    review_notes = models.TextField(
        blank=True,
        help_text="Notes from the reviewer",
    )

    # Related ledger entry if one was created during healing
    ledger_entry_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="ID of ledger entry created for this correction (if any)",
    )

    class Meta:
        indexes = [
            models.Index(fields=["resolution", "reviewed"]),
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["discrepancy_type"]),
            models.Index(fields=["run", "resolution"]),
        ]
        ordering = ["-created_at"]
        verbose_name_plural = "Reconciliation discrepancies"

    def __str__(self) -> str:
        return (
            f"Discrepancy({self.entity_type}:{self.entity_id}, {self.discrepancy_type})"
        )

    @property
    def needs_review(self) -> bool:
        """Check if this discrepancy needs human review."""
        return (
            self.resolution == DiscrepancyResolution.FLAGGED_FOR_REVIEW
            and not self.reviewed
        )


__all__ = [
    "ReconciliationRun",
    "ReconciliationRunStatus",
    "ReconciliationDiscrepancy",
    "DiscrepancyResolution",
]
