"""
Payout model for tracking money transfers to connected accounts.

A Payout represents money leaving the platform to a recipient's
Stripe Connected Account. One PaymentOrder can result in multiple
Payouts (e.g., service provider + referral bonus).

Usage:
    from payments.models import Payout
    from payments.state_machines import PayoutState

    # Create a payout after payment is captured
    payout = Payout.objects.create(
        payment_order=order,
        connected_account=mentor_account,
        amount_cents=4500,  # After platform fee
    )

    # State transitions using django-fsm
    payout.process()  # pending -> processing
    payout.save()

    # After Stripe transfer succeeds
    payout.complete()  # processing -> paid
    payout.save()
"""

from __future__ import annotations

from django.db import models
from django.db.models import F
from django.utils import timezone

from django_fsm import FSMField, transition

from core.models import BaseModel
from core.model_mixins import UUIDPrimaryKeyMixin

from payments.state_machines import PayoutState


class Payout(UUIDPrimaryKeyMixin, BaseModel):
    """
    Represents money transfer to a connected Stripe account.

    Tracks the lifecycle of an outbound transfer from pending
    through paid or failed states.

    State Flow:
        PENDING -> SCHEDULED -> PROCESSING -> SCHEDULED -> PAID
        PENDING -> PROCESSING -> SCHEDULED -> PAID (via webhooks)
        PROCESSING/SCHEDULED -> FAILED -> PENDING (retry)

    Webhook-driven state changes:
        transfer.created: PROCESSING -> SCHEDULED (Stripe confirms transfer queued)
        transfer.paid: PROCESSING/SCHEDULED -> PAID (transfer completed)
        transfer.failed: PROCESSING/SCHEDULED -> FAILED (transfer failed)

    Fields:
        payment_order: Source PaymentOrder for this payout
        connected_account: Destination Stripe Connected Account
        amount_cents: Payout amount in smallest currency unit
        currency: ISO 4217 currency code
        state: Current FSM state
        stripe_transfer_id: Stripe Transfer ID (tr_xxx)
        version: Optimistic locking version
        scheduled_for: Future datetime for scheduled payouts
        paid_at: When payout completed
        failed_at: When payout failed
        failure_reason: Error details if failed
        metadata: Flexible JSON storage

    Note:
        The payout lifecycle is independent from the PaymentOrder.
        A payment can be captured while the payout is scheduled
        for later.
    """

    # ==========================================================================
    # Relationships
    # ==========================================================================

    payment_order = models.ForeignKey(
        "payments.PaymentOrder",
        on_delete=models.PROTECT,
        related_name="payouts",
        null=True,
        blank=True,
        help_text="Payment order this payout originates from. Null for aggregated payouts.",
    )

    connected_account = models.ForeignKey(
        "payments.ConnectedAccount",
        on_delete=models.PROTECT,
        related_name="payouts",
        help_text="Connected account receiving the payout",
    )

    # ==========================================================================
    # Amount & Currency
    # ==========================================================================

    amount_cents = models.PositiveBigIntegerField(
        help_text="Payout amount in smallest currency unit (e.g., cents)",
    )

    currency = models.CharField(
        max_length=3,
        default="usd",
        help_text="ISO 4217 currency code (lowercase)",
    )

    # ==========================================================================
    # State
    # ==========================================================================

    state = FSMField(
        default=PayoutState.PENDING,
        choices=PayoutState.choices,
        db_index=True,
        protected=True,
        help_text="Current state of the payout (managed by FSM)",
    )

    # ==========================================================================
    # Stripe Integration
    # ==========================================================================

    stripe_transfer_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Stripe Transfer ID (tr_xxx)",
    )

    # ==========================================================================
    # Concurrency Control
    # ==========================================================================

    version = models.PositiveIntegerField(
        default=1,
        help_text="Version for optimistic locking - incremented on each save",
    )

    # ==========================================================================
    # Scheduling & Timestamps
    # ==========================================================================

    scheduled_for = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the payout is scheduled to be sent",
    )

    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When payout was completed",
    )

    failed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When payout failed",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When payout was cancelled (e.g., for refund processing)",
    )

    # ==========================================================================
    # Metadata & Error Info
    # ==========================================================================

    failure_reason = models.TextField(
        null=True,
        blank=True,
        help_text="Detailed reason if payout failed",
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
        verbose_name = "Payout"
        verbose_name_plural = "Payouts"
        indexes = [
            models.Index(fields=["payment_order", "state"]),
            models.Index(fields=["connected_account", "state"]),
            models.Index(fields=["state", "scheduled_for"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount_cents__gt=0),
                name="payout_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        """Return string representation with ID, state, and amount."""
        amount_display = f"{self.amount_cents / 100:.2f} {self.currency.upper()}"
        return f"Payout({self.id}, {self.state}, {amount_display})"

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
        source=PayoutState.PENDING,
        target=PayoutState.SCHEDULED,
    )
    def schedule(self, scheduled_for: timezone.datetime):
        """
        Schedule the payout for future processing.

        Transition: PENDING -> SCHEDULED

        Use for delayed payouts (e.g., after hold period).

        Args:
            scheduled_for: When to process the payout
        """
        self.scheduled_for = scheduled_for

    @transition(
        field=state,
        source=[PayoutState.PENDING, PayoutState.SCHEDULED],
        target=PayoutState.PROCESSING,
    )
    def process(self):
        """
        Begin processing the payout.

        Transition: PENDING/SCHEDULED -> PROCESSING

        Called when the Stripe Transfer API call is initiated.
        The transfer_id should be set after a successful API call.
        """
        pass

    @transition(
        field=state,
        source=PayoutState.PROCESSING,
        target=PayoutState.SCHEDULED,
    )
    def mark_scheduled(self):
        """
        Mark payout as scheduled in Stripe's system.

        Transition: PROCESSING -> SCHEDULED

        Called when transfer.created webhook confirms Stripe has
        queued the transfer for processing. This indicates Stripe
        has accepted the transfer and will process it.
        """
        pass

    @transition(
        field=state,
        source=[PayoutState.PROCESSING, PayoutState.SCHEDULED],
        target=PayoutState.PAID,
    )
    def complete(self):
        """
        Mark payout as completed.

        Transition: PROCESSING/SCHEDULED -> PAID

        Called when Stripe confirms the transfer was successful
        (transfer.paid webhook).
        """
        self.paid_at = timezone.now()

    @transition(
        field=state,
        source=[PayoutState.PROCESSING, PayoutState.SCHEDULED],
        target=PayoutState.FAILED,
    )
    def fail(self, reason: str | None = None):
        """
        Mark payout as failed.

        Transition: PROCESSING/SCHEDULED -> FAILED

        Called when Stripe reports the transfer failed
        (transfer.failed webhook).

        Args:
            reason: Optional failure reason for debugging
        """
        self.failed_at = timezone.now()
        if reason:
            self.failure_reason = reason

    @transition(
        field=state,
        source=PayoutState.FAILED,
        target=PayoutState.PENDING,
    )
    def retry(self):
        """
        Retry a failed payout.

        Transition: FAILED -> PENDING

        Resets failure state to allow another attempt.
        """
        self.failed_at = None
        self.failure_reason = None
        self.stripe_transfer_id = None

    @transition(
        field=state,
        source=[PayoutState.PENDING, PayoutState.SCHEDULED],
        target=PayoutState.CANCELLED,
    )
    def cancel(self, reason: str | None = None):
        """
        Cancel a pending or scheduled payout.

        Transition: PENDING/SCHEDULED -> CANCELLED

        Used during refund processing when funds need to be returned
        to the customer instead of paid out to the recipient.

        Args:
            reason: Optional reason for cancellation
        """
        self.cancelled_at = timezone.now()
        if reason:
            self.failure_reason = reason

    # ==========================================================================
    # Properties
    # ==========================================================================

    @property
    def is_complete(self) -> bool:
        """Check if payout is complete."""
        return self.state == PayoutState.PAID

    @property
    def is_pending(self) -> bool:
        """Check if payout is pending processing."""
        return self.state in [PayoutState.PENDING, PayoutState.SCHEDULED]

    @property
    def can_retry(self) -> bool:
        """Check if payout can be retried."""
        return self.state == PayoutState.FAILED

    @property
    def can_cancel(self) -> bool:
        """Check if payout can be cancelled (for refund processing)."""
        return self.state in [PayoutState.PENDING, PayoutState.SCHEDULED]

    @property
    def is_cancelled(self) -> bool:
        """Check if payout was cancelled."""
        return self.state == PayoutState.CANCELLED
