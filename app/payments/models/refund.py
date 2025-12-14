"""
Refund model for tracking money returned to customers.

A Refund represents money going back to the customer from a completed
payment. Supports both full and partial refunds. One PaymentOrder
can have multiple Refunds for partial refund scenarios.

Usage:
    from payments.models import Refund
    from payments.state_machines import RefundState

    # Create a refund request
    refund = Refund.objects.create(
        payment_order=order,
        amount_cents=2500,  # Partial refund
        reason="Customer requested cancellation",
    )

    # State transitions using django-fsm
    refund.process()  # requested -> processing
    refund.save()

    # After Stripe refund succeeds
    refund.complete()  # processing -> completed
    refund.save()
"""

from __future__ import annotations

from django.db import models
from django.db.models import F
from django.utils import timezone

from django_fsm import FSMField, transition

from core.models import BaseModel
from core.model_mixins import UUIDPrimaryKeyMixin

from payments.state_machines import RefundState


class Refund(UUIDPrimaryKeyMixin, BaseModel):
    """
    Represents money returned to a customer.

    Tracks the refund lifecycle from request through completion
    or failure. Supports both full and partial refunds.

    State Flow:
        REQUESTED -> PROCESSING -> COMPLETED
        REQUESTED -> PROCESSING -> FAILED

    Fields:
        payment_order: Source PaymentOrder being refunded
        amount_cents: Refund amount in smallest currency unit
        currency: ISO 4217 currency code
        reason: Customer/admin-facing refund reason
        state: Current FSM state
        stripe_refund_id: Stripe Refund ID (re_xxx)
        version: Optimistic locking version
        completed_at: When refund completed
        failed_at: When refund failed
        failure_reason: Error details if failed
        metadata: Flexible JSON storage

    Note:
        Multiple refunds can exist for a PaymentOrder for partial
        refund scenarios. Total refunded amount should not exceed
        the original payment amount.
    """

    # ==========================================================================
    # Relationships
    # ==========================================================================

    payment_order = models.ForeignKey(
        "payments.PaymentOrder",
        on_delete=models.PROTECT,
        related_name="refunds",
        help_text="Payment order being refunded",
    )

    # ==========================================================================
    # Amount & Currency
    # ==========================================================================

    amount_cents = models.PositiveBigIntegerField(
        help_text="Refund amount in smallest currency unit (e.g., cents)",
    )

    currency = models.CharField(
        max_length=3,
        default="usd",
        help_text="ISO 4217 currency code (lowercase)",
    )

    # ==========================================================================
    # Refund Details
    # ==========================================================================

    reason = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Reason for the refund (visible to customer)",
    )

    # ==========================================================================
    # State
    # ==========================================================================

    state = FSMField(
        default=RefundState.REQUESTED,
        choices=RefundState.choices,
        db_index=True,
        protected=True,
        help_text="Current state of the refund (managed by FSM)",
    )

    # ==========================================================================
    # Stripe Integration
    # ==========================================================================

    stripe_refund_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Stripe Refund ID (re_xxx)",
    )

    # ==========================================================================
    # Concurrency Control
    # ==========================================================================

    version = models.PositiveIntegerField(
        default=1,
        help_text="Version for optimistic locking - incremented on each save",
    )

    # ==========================================================================
    # Timestamps
    # ==========================================================================

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When refund was completed",
    )

    failed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When refund failed",
    )

    # ==========================================================================
    # Metadata & Error Info
    # ==========================================================================

    failure_reason = models.TextField(
        null=True,
        blank=True,
        help_text="Detailed reason if refund failed",
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary JSON metadata for extensibility",
    )

    # ==========================================================================
    # Meta & Methods
    # ==========================================================================

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Refund"
        verbose_name_plural = "Refunds"
        indexes = [
            models.Index(fields=["payment_order", "state"]),
            models.Index(fields=["state", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount_cents__gt=0),
                name="refund_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        """Return string representation with ID, state, and amount."""
        amount_display = f"{self.amount_cents / 100:.2f} {self.currency.upper()}"
        return f"Refund({self.id}, {self.state}, {amount_display})"

    def save(self, *args, **kwargs):
        """
        Save with version auto-increment for optimistic locking.
        """
        is_update = self.pk and not kwargs.get("force_insert", False)
        if is_update:
            self.version = F("version") + 1
        super().save(*args, **kwargs)
        if is_update:
            self.refresh_from_db(fields=["version"])

    # ==========================================================================
    # State Transitions (django-fsm)
    # ==========================================================================

    @transition(
        field=state,
        source=RefundState.REQUESTED,
        target=RefundState.PROCESSING,
    )
    def process(self):
        """
        Begin processing the refund.

        Transition: REQUESTED -> PROCESSING

        Called when the Stripe Refund API call is initiated.
        The refund_id should be set after a successful API call.
        """
        pass

    @transition(
        field=state,
        source=RefundState.PROCESSING,
        target=RefundState.COMPLETED,
    )
    def complete(self):
        """
        Mark refund as completed.

        Transition: PROCESSING -> COMPLETED

        Called when Stripe confirms the refund was successful.
        """
        self.completed_at = timezone.now()

    @transition(
        field=state,
        source=RefundState.PROCESSING,
        target=RefundState.FAILED,
    )
    def fail(self, reason: str | None = None):
        """
        Mark refund as failed.

        Transition: PROCESSING -> FAILED

        Called when Stripe reports the refund failed.

        Args:
            reason: Optional failure reason for debugging
        """
        self.failed_at = timezone.now()
        if reason:
            self.failure_reason = reason

    # ==========================================================================
    # Properties
    # ==========================================================================

    @property
    def is_complete(self) -> bool:
        """Check if refund is complete."""
        return self.state == RefundState.COMPLETED

    @property
    def is_pending(self) -> bool:
        """Check if refund is pending processing."""
        return self.state in [RefundState.REQUESTED, RefundState.PROCESSING]
