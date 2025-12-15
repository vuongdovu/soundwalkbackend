"""
PaymentOrder and FundHold models for payment lifecycle management.

PaymentOrder is the central entity tracking a payment from initiation
through final settlement. FundHold tracks escrow holds for payments
awaiting service completion.

Usage:
    from payments.models import PaymentOrder, FundHold
    from payments.state_machines import PaymentOrderState, PaymentStrategyType

    # Create a new payment order
    order = PaymentOrder.objects.create(
        payer=user,
        amount_cents=5000,
        strategy_type=PaymentStrategyType.ESCROW,
        reference_id=booking.id,
        reference_type="booking",
    )

    # State transitions using django-fsm
    order.submit()  # draft -> pending
    order.save()

    # Create fund hold for escrow
    hold = FundHold.objects.create(
        payment_order=order,
        amount_cents=5000,
        expires_at=timezone.now() + timedelta(days=7),
    )
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import F
from django.utils import timezone

from django_fsm import FSMField, transition

from core.models import BaseModel
from core.model_mixins import UUIDPrimaryKeyMixin

from payments.state_machines import (
    PaymentOrderState,
    PaymentStrategyType,
)


class PaymentOrder(UUIDPrimaryKeyMixin, BaseModel):
    """
    Central payment entity tracking the full payment lifecycle.

    Uses django-fsm for state machine management and optimistic
    locking via version field for concurrency control.

    State Flow (Direct Payment):
        DRAFT -> PENDING -> PROCESSING -> CAPTURED -> SETTLED

    State Flow (Escrow Payment):
        DRAFT -> PENDING -> PROCESSING -> CAPTURED -> HELD -> RELEASED -> SETTLED

    Recovery Flow:
        FAILED -> PENDING (retry)

    Cancellation Flow:
        DRAFT/PENDING -> CANCELLED

    Refund Flow:
        CAPTURED/SETTLED -> REFUNDED/PARTIALLY_REFUNDED

    Fields:
        payer: User making the payment
        amount_cents: Payment amount in smallest currency unit
        currency: ISO 4217 currency code
        strategy_type: Payment processing strategy
        state: Current FSM state
        reference_id/type: Generic reference to business entity
        stripe_payment_intent_id: Stripe PaymentIntent ID
        version: Optimistic locking version
        *_at timestamps: Track state transition times
        metadata: Flexible JSON storage
        failure_reason: Error details if payment failed

    Note:
        The version field is auto-incremented on save for optimistic
        locking. Use check_version() to safely update with concurrency
        protection.
    """

    # ==========================================================================
    # Relationships
    # ==========================================================================

    payer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payment_orders",
        help_text="User making the payment",
    )

    # ==========================================================================
    # Amount & Currency
    # ==========================================================================

    amount_cents = models.PositiveBigIntegerField(
        help_text="Payment amount in smallest currency unit (e.g., cents)",
    )

    currency = models.CharField(
        max_length=3,
        default="usd",
        help_text="ISO 4217 currency code (lowercase)",
    )

    # ==========================================================================
    # Strategy & State
    # ==========================================================================

    strategy_type = models.CharField(
        max_length=20,
        choices=PaymentStrategyType.choices,
        default=PaymentStrategyType.DIRECT,
        help_text="Payment processing strategy (direct, escrow, subscription)",
    )

    state = FSMField(
        default=PaymentOrderState.DRAFT,
        choices=PaymentOrderState.choices,
        db_index=True,
        protected=True,  # Prevent direct assignment outside transitions
        help_text="Current state of the payment order (managed by FSM)",
    )

    # ==========================================================================
    # Generic Reference Pattern
    # ==========================================================================

    reference_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="UUID of related business entity (e.g., booking ID)",
    )

    reference_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Type of related entity (e.g., 'booking', 'session')",
    )

    # ==========================================================================
    # Subscription Relationship (for recurring payments)
    # ==========================================================================

    subscription = models.ForeignKey(
        "payments.Subscription",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_orders",
        help_text="Subscription this payment belongs to (for recurring payments)",
    )

    # ==========================================================================
    # Stripe Integration
    # ==========================================================================

    stripe_payment_intent_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Stripe PaymentIntent ID (pi_xxx)",
    )

    stripe_invoice_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Stripe Invoice ID (in_xxx) - for subscription payments",
    )

    # ==========================================================================
    # Concurrency Control
    # ==========================================================================

    version = models.PositiveIntegerField(
        default=1,
        help_text="Version for optimistic locking - incremented on each save",
    )

    # ==========================================================================
    # State Timestamps
    # ==========================================================================

    captured_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When payment was captured from customer",
    )

    held_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When funds were placed in escrow hold",
    )

    released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When funds were released from escrow",
    )

    settled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When payment was fully settled",
    )

    failed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When payment failed",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When payment was cancelled",
    )

    refunded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When first refund was processed (full or partial)",
    )

    # ==========================================================================
    # Metadata & Error Info
    # ==========================================================================

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary JSON metadata for extensibility",
    )

    failure_reason = models.TextField(
        null=True,
        blank=True,
        help_text="Detailed reason if payment failed",
    )

    # ==========================================================================
    # Meta & Methods
    # ==========================================================================

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Payment Order"
        verbose_name_plural = "Payment Orders"
        indexes = [
            models.Index(fields=["reference_type", "reference_id"]),
            models.Index(fields=["payer", "state"]),
            models.Index(fields=["payer", "created_at"]),
            models.Index(fields=["subscription", "state"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount_cents__gt=0),
                name="payment_order_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        """Return string representation with ID, state, and amount."""
        amount_display = f"{self.amount_cents / 100:.2f} {self.currency.upper()}"
        return f"PaymentOrder({self.id}, {self.state}, {amount_display})"

    def save(self, *args, **kwargs):
        """
        Save with version auto-increment for optimistic locking.

        On update (not force_insert), atomically increments the version
        field to detect concurrent modifications.
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
        source=PaymentOrderState.DRAFT,
        target=PaymentOrderState.PENDING,
    )
    def submit(self):
        """
        Submit the payment order for processing.

        Transition: DRAFT -> PENDING

        Called when the PaymentIntent is created and sent to Stripe.
        The customer can now complete payment (card input, 3DS, etc.).
        """
        pass

    @transition(
        field=state,
        source=PaymentOrderState.PENDING,
        target=PaymentOrderState.PROCESSING,
    )
    def process(self):
        """
        Begin processing the payment.

        Transition: PENDING -> PROCESSING

        Called when customer has submitted payment details and
        Stripe is processing the charge.
        """
        pass

    @transition(
        field=state,
        source=PaymentOrderState.PROCESSING,
        target=PaymentOrderState.CAPTURED,
    )
    def capture(self):
        """
        Mark payment as captured.

        Transition: PROCESSING -> CAPTURED

        Called when Stripe confirms the payment was successful.
        For direct payments, typically followed by settle_from_captured().
        """
        self.captured_at = timezone.now()

    @transition(
        field=state,
        source=PaymentOrderState.CAPTURED,
        target=PaymentOrderState.HELD,
    )
    def hold(self):
        """
        Put captured payment into escrow hold.

        Transition: CAPTURED -> HELD

        Called for escrow payments after capture. A FundHold record
        should be created when this transition occurs.
        """
        self.held_at = timezone.now()

    @transition(
        field=state,
        source=PaymentOrderState.HELD,
        target=PaymentOrderState.RELEASED,
    )
    def release(self):
        """
        Release funds from escrow hold.

        Transition: HELD -> RELEASED

        Called when service is completed and funds can be released
        to the service provider. Creates a Payout record.
        """
        self.released_at = timezone.now()

    @transition(
        field=state,
        source=PaymentOrderState.CAPTURED,
        target=PaymentOrderState.SETTLED,
    )
    def settle_from_captured(self):
        """
        Settle a captured payment (direct path).

        Transition: CAPTURED -> SETTLED

        For direct payments that skip the escrow hold phase.
        """
        self.settled_at = timezone.now()

    @transition(
        field=state,
        source=PaymentOrderState.RELEASED,
        target=PaymentOrderState.SETTLED,
    )
    def settle_from_released(self):
        """
        Settle a released payment (escrow path).

        Transition: RELEASED -> SETTLED

        Called when the payout to the service provider completes.
        """
        self.settled_at = timezone.now()

    @transition(
        field=state,
        source=PaymentOrderState.PROCESSING,
        target=PaymentOrderState.FAILED,
    )
    def fail(self, reason: str | None = None):
        """
        Mark payment as failed.

        Transition: PROCESSING -> FAILED

        Called when Stripe reports payment failure (card declined,
        insufficient funds, etc.).

        Args:
            reason: Optional failure reason for debugging
        """
        self.failed_at = timezone.now()
        if reason:
            self.failure_reason = reason

    @transition(
        field=state,
        source=PaymentOrderState.FAILED,
        target=PaymentOrderState.PENDING,
    )
    def retry(self):
        """
        Retry a failed payment.

        Transition: FAILED -> PENDING

        Allows the customer to attempt payment again with
        different payment details.
        """
        self.failed_at = None
        self.failure_reason = None

    @transition(
        field=state,
        source=[PaymentOrderState.DRAFT, PaymentOrderState.PENDING],
        target=PaymentOrderState.CANCELLED,
    )
    def cancel(self):
        """
        Cancel the payment order.

        Transition: DRAFT/PENDING -> CANCELLED

        Can only cancel before payment is processed. Once processing
        starts, must use refund instead.
        """
        self.cancelled_at = timezone.now()

    @transition(
        field=state,
        source=[
            PaymentOrderState.CAPTURED,
            PaymentOrderState.HELD,
            PaymentOrderState.RELEASED,
            PaymentOrderState.SETTLED,
            PaymentOrderState.PARTIALLY_REFUNDED,
        ],
        target=PaymentOrderState.REFUNDED,
    )
    def refund_full(self):
        """
        Mark as fully refunded.

        Transition: CAPTURED/HELD/RELEASED/SETTLED/PARTIALLY_REFUNDED -> REFUNDED

        Called when a full refund is processed, or when the final
        partial refund exhausts the remaining amount.

        Note:
            For HELD state (escrow), the full amount including
            platform fee is refunded since fee hasn't been taken yet.
            For RELEASED state, payout must be cancelled first.
        """
        self.refunded_at = timezone.now()

    @transition(
        field=state,
        source=[
            PaymentOrderState.CAPTURED,
            PaymentOrderState.HELD,
            PaymentOrderState.RELEASED,
            PaymentOrderState.SETTLED,
            PaymentOrderState.PARTIALLY_REFUNDED,
        ],
        target=PaymentOrderState.PARTIALLY_REFUNDED,
    )
    def refund_partial(self):
        """
        Mark as partially refunded.

        Transition: CAPTURED/HELD/RELEASED/SETTLED/PARTIALLY_REFUNDED -> PARTIALLY_REFUNDED

        Called when a partial refund is processed. The payment
        remains otherwise in its current state but marked as
        partially refunded.

        Note:
            Multiple partial refunds are allowed. Each creates
            a new Refund record. Total refunds must not exceed
            original payment amount.
        """
        if self.refunded_at is None:
            self.refunded_at = timezone.now()


class FundHold(UUIDPrimaryKeyMixin, BaseModel):
    """
    Represents funds held in escrow for a payment order.

    Used for escrow-style payments where funds are captured but held
    until service completion before being released to the provider.

    Lifecycle:
        1. PaymentOrder reaches CAPTURED state
        2. FundHold created with expiration time
        3. Service is delivered
        4. FundHold released, Payout created
        5. PaymentOrder transitions to RELEASED -> SETTLED

    Expiration Handling:
        - If expires_at passes without release, a worker checks
          the configured policy (auto-release, auto-refund, escalate)
        - The strategy determines expiration behavior

    Fields:
        payment_order: The PaymentOrder these funds belong to
        amount_cents: Amount held in smallest currency unit
        currency: ISO 4217 currency code
        expires_at: When the hold expires
        released: Whether the hold has been released
        released_at: When the hold was released
        released_to_payout: The Payout created from this hold
        version: Optimistic locking version
        metadata: Flexible JSON storage

    Note:
        Multiple FundHolds can exist for a PaymentOrder if the
        amount is split (e.g., partial releases).
    """

    payment_order = models.ForeignKey(
        PaymentOrder,
        on_delete=models.PROTECT,
        related_name="fund_holds",
        help_text="Payment order this hold belongs to",
    )

    amount_cents = models.PositiveBigIntegerField(
        help_text="Amount held in smallest currency unit (e.g., cents)",
    )

    currency = models.CharField(
        max_length=3,
        default="usd",
        help_text="ISO 4217 currency code (lowercase)",
    )

    expires_at = models.DateTimeField(
        help_text="When this hold expires and requires action",
    )

    released = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether the hold has been released",
    )

    released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the hold was released",
    )

    # Forward reference to Payout (created after FundHold is released)
    released_to_payout = models.ForeignKey(
        "payments.Payout",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_holds",
        help_text="Payout that received the released funds",
    )

    version = models.PositiveIntegerField(
        default=1,
        help_text="Version for optimistic locking - incremented on each save",
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary JSON metadata (e.g., release conditions)",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Fund Hold"
        verbose_name_plural = "Fund Holds"
        indexes = [
            models.Index(fields=["released", "expires_at"]),
            models.Index(fields=["payment_order", "released"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount_cents__gt=0),
                name="fund_hold_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        """Return string representation with amount and release status."""
        amount_display = f"{self.amount_cents / 100:.2f} {self.currency.upper()}"
        status = "released" if self.released else "held"
        return f"FundHold({self.id}, {amount_display}, {status})"

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

    @property
    def is_expired(self) -> bool:
        """
        Check if the hold has expired.

        Returns:
            True if current time is past expires_at and not released
        """
        return not self.released and timezone.now() > self.expires_at

    def release_to(self, payout: models.Model) -> None:
        """
        Mark the hold as released to a specific payout.

        Args:
            payout: The Payout model instance receiving the funds

        Note:
            This method does not save - caller must save after calling.
        """
        self.released = True
        self.released_at = timezone.now()
        self.released_to_payout = payout
