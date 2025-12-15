"""
Subscription model for recurring payment management.

Subscription tracks an ongoing subscription relationship between a subscriber
and a recipient. Each billing cycle creates a PaymentOrder linked
to this Subscription.

Usage:
    from payments.models import Subscription
    from payments.state_machines import SubscriptionState

    # Create a new subscription
    subscription = Subscription.objects.create(
        payer=subscriber,
        recipient_profile_id=recipient_profile.pk,  # Profile uses user as primary key
        stripe_subscription_id="sub_xxx",
        stripe_customer_id="cus_xxx",
        stripe_price_id="price_xxx",
        amount_cents=10000,
        currency="usd",
        billing_interval="month",
    )

    # State transitions using django-fsm
    subscription.activate()  # pending -> active
    subscription.save()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.db.models import F
from django.utils import timezone

from django_fsm import FSMField, transition

from core.models import BaseModel
from core.model_mixins import UUIDPrimaryKeyMixin

from payments.state_machines import SubscriptionState

if TYPE_CHECKING:
    pass


class Subscription(UUIDPrimaryKeyMixin, BaseModel):
    """
    Tracks a recurring subscription relationship.

    Uses django-fsm for state machine management and optimistic
    locking via version field for concurrency control.

    State Flow:
        PENDING -> ACTIVE (on first successful payment)
        ACTIVE -> PAST_DUE (on payment failure)
        PAST_DUE -> ACTIVE (on successful retry payment)
        ACTIVE -> CANCELLED (immediate cancellation)
        PAST_DUE -> CANCELLED (after max retries or explicit cancellation)

    Fields:
        payer: User paying for the subscription
        recipient_profile_id: Profile UUID of the subscription recipient
        stripe_subscription_id: Stripe Subscription ID (sub_xxx)
        stripe_customer_id: Stripe Customer ID (cus_xxx)
        stripe_price_id: Stripe Price ID (price_xxx)
        amount_cents: Subscription amount in smallest currency unit
        currency: ISO 4217 currency code
        billing_interval: Billing frequency ('month' or 'year')
        state: Current FSM state
        current_period_start/end: Current billing period timestamps
        cancel_at_period_end: Whether cancellation is scheduled
        cancelled_at: When subscription was cancelled
        last_invoice_id: Most recent Stripe invoice ID
        last_payment_at: When last successful payment occurred
        version: Optimistic locking version
        metadata: Flexible JSON storage
    """

    # ==========================================================================
    # Relationships
    # ==========================================================================

    payer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="subscriptions",
        help_text="User paying for the subscription",
    )

    # Using UUID instead of FK to Profile to avoid circular imports
    # and allow flexibility if profile model changes
    recipient_profile_id = models.UUIDField(
        db_index=True,
        help_text="Profile UUID of the subscription recipient",
    )

    # ==========================================================================
    # Stripe Integration
    # ==========================================================================

    stripe_subscription_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Stripe Subscription ID (sub_xxx)",
    )

    stripe_customer_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Stripe Customer ID (cus_xxx)",
    )

    stripe_price_id = models.CharField(
        max_length=255,
        help_text="Stripe Price ID (price_xxx)",
    )

    # ==========================================================================
    # Amount & Currency
    # ==========================================================================

    amount_cents = models.PositiveBigIntegerField(
        help_text="Subscription amount in smallest currency unit (e.g., cents)",
    )

    currency = models.CharField(
        max_length=3,
        default="usd",
        help_text="ISO 4217 currency code (lowercase)",
    )

    billing_interval = models.CharField(
        max_length=10,
        default="month",
        help_text="Billing frequency: 'month' or 'year'",
    )

    # ==========================================================================
    # State
    # ==========================================================================

    state = FSMField(
        default=SubscriptionState.PENDING,
        choices=SubscriptionState.choices,
        db_index=True,
        protected=True,
        help_text="Current state of the subscription (managed by FSM)",
    )

    # ==========================================================================
    # Billing Period
    # ==========================================================================

    current_period_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Start of current billing period",
    )

    current_period_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="End of current billing period",
    )

    # ==========================================================================
    # Cancellation
    # ==========================================================================

    cancel_at_period_end = models.BooleanField(
        default=False,
        help_text="Whether subscription will cancel at period end",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When subscription was cancelled",
    )

    # ==========================================================================
    # Payment Tracking
    # ==========================================================================

    last_invoice_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Most recent Stripe invoice ID",
    )

    last_payment_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When last successful payment occurred",
    )

    # ==========================================================================
    # Concurrency Control
    # ==========================================================================

    version = models.PositiveIntegerField(
        default=1,
        help_text="Version for optimistic locking - incremented on each save",
    )

    # ==========================================================================
    # Metadata
    # ==========================================================================

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
        verbose_name = "Subscription"
        verbose_name_plural = "Subscriptions"
        indexes = [
            models.Index(fields=["payer", "state"]),
            models.Index(fields=["recipient_profile_id", "state"]),
            models.Index(fields=["stripe_customer_id"]),
            models.Index(fields=["state", "current_period_end"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount_cents__gt=0),
                name="subscription_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        """Return string representation with ID, state, and amount."""
        amount_display = f"{self.amount_cents / 100:.2f} {self.currency.upper()}"
        return f"Subscription({self.id}, {self.state}, {amount_display}/{self.billing_interval})"

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
        source=SubscriptionState.PENDING,
        target=SubscriptionState.ACTIVE,
    )
    def activate(self):
        """
        Activate the subscription after first successful payment.

        Transition: PENDING -> ACTIVE
        """
        pass

    @transition(
        field=state,
        source=SubscriptionState.ACTIVE,
        target=SubscriptionState.PAST_DUE,
    )
    def mark_past_due(self):
        """
        Mark subscription as past due after payment failure.

        Transition: ACTIVE -> PAST_DUE

        Stripe Smart Retries will attempt to collect payment.
        """
        pass

    @transition(
        field=state,
        source=SubscriptionState.PAST_DUE,
        target=SubscriptionState.ACTIVE,
    )
    def reactivate(self):
        """
        Reactivate subscription after successful retry payment.

        Transition: PAST_DUE -> ACTIVE
        """
        pass

    @transition(
        field=state,
        source=[SubscriptionState.ACTIVE, SubscriptionState.PAST_DUE],
        target=SubscriptionState.CANCELLED,
    )
    def cancel(self):
        """
        Cancel the subscription.

        Transition: ACTIVE/PAST_DUE -> CANCELLED

        Can be triggered by:
        - User cancellation
        - Max payment retries exceeded
        - customer.subscription.deleted webhook
        """
        self.cancelled_at = timezone.now()

    # ==========================================================================
    # Helper Properties
    # ==========================================================================

    @property
    def is_active(self) -> bool:
        """Check if subscription is currently active."""
        return self.state == SubscriptionState.ACTIVE

    @property
    def is_cancelled(self) -> bool:
        """Check if subscription is cancelled."""
        return self.state == SubscriptionState.CANCELLED

    @property
    def is_past_due(self) -> bool:
        """Check if subscription is past due."""
        return self.state == SubscriptionState.PAST_DUE

    @property
    def will_cancel_at_period_end(self) -> bool:
        """Check if subscription is scheduled for cancellation."""
        return self.cancel_at_period_end and not self.is_cancelled
