"""
Payment models for Stripe integration.

This module defines models for:
- Subscription: User subscription state and Stripe mapping
- Transaction: Payment history and audit trail
- WebhookEvent: Stripe webhook event tracking

Related files:
    - services.py: StripeService for business logic
    - webhooks.py: Webhook event handlers
    - tasks.py: Async payment processing

Model Relationships:
    User (1) ---> (1) Subscription
    User (1) ---> (*) Transaction
    Subscription (1) ---> (*) Transaction (optional)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

from core.models import BaseModel

if TYPE_CHECKING:
    pass


class SubscriptionStatus(models.TextChoices):
    """
    Stripe subscription status values.

    Maps to Stripe's subscription.status field.
    See: https://stripe.com/docs/api/subscriptions/object#subscription_object-status
    """

    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past Due"
    CANCELED = "canceled", "Canceled"
    TRIALING = "trialing", "Trialing"
    PAUSED = "paused", "Paused"
    INCOMPLETE = "incomplete", "Incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired", "Incomplete Expired"
    UNPAID = "unpaid", "Unpaid"


class TransactionType(models.TextChoices):
    """Types of payment transactions."""

    CHARGE = "charge", "Charge"
    REFUND = "refund", "Refund"
    SUBSCRIPTION = "subscription", "Subscription Payment"
    CREDIT = "credit", "Credit"


class TransactionStatus(models.TextChoices):
    """Transaction processing status."""

    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially Refunded"


class WebhookProcessingStatus(models.TextChoices):
    """Webhook event processing status."""

    PENDING = "pending", "Pending"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"
    IGNORED = "ignored", "Ignored"


class Subscription(BaseModel):
    """
    User subscription linked to Stripe.

    Tracks the user's subscription state, plan details,
    and billing period information.

    Fields:
        user: OneToOne link to User (one subscription per user)
        stripe_customer_id: Stripe Customer ID (cus_xxx)
        stripe_subscription_id: Stripe Subscription ID (sub_xxx)
        stripe_price_id: Stripe Price ID (price_xxx)
        status: Current subscription status
        plan_name: Human-readable plan name
        current_period_start/end: Current billing period
        cancel_at_period_end: Whether subscription cancels at period end
        canceled_at: When cancellation was requested
        trial_start/end: Trial period dates
        metadata: Additional plan features (ai_requests_limit, storage_gb)

    Indexes:
        - stripe_customer_id (unique)
        - stripe_subscription_id (unique, nullable)
        - stripe_price_id
        - status

    Usage:
        subscription = Subscription.objects.get(user=user)
        if subscription.is_active:
            # Grant access to features
            features = subscription.metadata.get("features", {})
    """

    # TODO: Implement model fields
    # user = models.OneToOneField(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.CASCADE,
    #     related_name="subscription",
    # )
    # stripe_customer_id = models.CharField(
    #     max_length=255,
    #     unique=True,
    #     db_index=True,
    #     help_text="Stripe Customer ID (cus_xxx)",
    # )
    # stripe_subscription_id = models.CharField(
    #     max_length=255,
    #     unique=True,
    #     null=True,
    #     blank=True,
    #     db_index=True,
    #     help_text="Stripe Subscription ID (sub_xxx)",
    # )
    # stripe_price_id = models.CharField(
    #     max_length=255,
    #     db_index=True,
    #     help_text="Stripe Price ID (price_xxx)",
    # )
    # status = models.CharField(
    #     max_length=20,
    #     choices=SubscriptionStatus.choices,
    #     default=SubscriptionStatus.INCOMPLETE,
    #     db_index=True,
    # )
    # plan_name = models.CharField(max_length=100)
    # current_period_start = models.DateTimeField(null=True, blank=True)
    # current_period_end = models.DateTimeField(null=True, blank=True)
    # cancel_at_period_end = models.BooleanField(default=False)
    # canceled_at = models.DateTimeField(null=True, blank=True)
    # trial_start = models.DateTimeField(null=True, blank=True)
    # trial_end = models.DateTimeField(null=True, blank=True)
    # metadata = models.JSONField(
    #     default=dict,
    #     blank=True,
    #     help_text="Plan features: ai_requests_limit, storage_gb, etc.",
    # )

    class Meta:
        verbose_name = "Subscription"
        verbose_name_plural = "Subscriptions"
        # indexes = [
        #     models.Index(fields=["status", "current_period_end"]),
        # ]

    def __str__(self) -> str:
        # TODO: Implement
        # return f"{self.user.email} - {self.plan_name} ({self.status})"
        return "Subscription"

    # TODO: Implement properties
    # @property
    # def is_active(self) -> bool:
    #     """Check if subscription is currently active."""
    #     return self.status in [
    #         SubscriptionStatus.ACTIVE,
    #         SubscriptionStatus.TRIALING,
    #     ]
    #
    # @property
    # def is_trialing(self) -> bool:
    #     """Check if subscription is in trial period."""
    #     return self.status == SubscriptionStatus.TRIALING
    #
    # @property
    # def will_cancel(self) -> bool:
    #     """Check if subscription will cancel at period end."""
    #     return self.cancel_at_period_end and self.is_active


class Transaction(BaseModel):
    """
    Payment transaction record.

    Stores all payment events for audit trail and reporting.
    Linked to Stripe PaymentIntent, Invoice, and Charge objects.

    Fields:
        user: ForeignKey to User (nullable for deleted users)
        subscription: Optional link to subscription
        stripe_payment_intent_id: Stripe PaymentIntent ID (pi_xxx)
        stripe_invoice_id: Stripe Invoice ID (in_xxx)
        stripe_charge_id: Stripe Charge ID (ch_xxx)
        transaction_type: Type of transaction
        status: Current transaction status
        amount_cents: Amount in cents (avoid floating point)
        currency: ISO currency code (usd, eur, etc.)
        description: Human-readable description
        metadata: Additional transaction data
        failure_code/message: Stripe failure details

    Indexes:
        - stripe_payment_intent_id (unique, nullable)
        - stripe_invoice_id
        - user + created_at
        - status + created_at

    Usage:
        transactions = Transaction.objects.filter(
            user=user,
            status=TransactionStatus.SUCCEEDED
        ).order_by("-created_at")
    """

    # TODO: Implement model fields
    # user = models.ForeignKey(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     related_name="transactions",
    # )
    # subscription = models.ForeignKey(
    #     Subscription,
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     blank=True,
    #     related_name="transactions",
    # )
    # stripe_payment_intent_id = models.CharField(
    #     max_length=255,
    #     unique=True,
    #     null=True,
    #     blank=True,
    #     db_index=True,
    # )
    # stripe_invoice_id = models.CharField(
    #     max_length=255,
    #     null=True,
    #     blank=True,
    #     db_index=True,
    # )
    # stripe_charge_id = models.CharField(
    #     max_length=255,
    #     null=True,
    #     blank=True,
    # )
    # transaction_type = models.CharField(
    #     max_length=20,
    #     choices=TransactionType.choices,
    # )
    # status = models.CharField(
    #     max_length=20,
    #     choices=TransactionStatus.choices,
    #     default=TransactionStatus.PENDING,
    # )
    # amount_cents = models.IntegerField(
    #     help_text="Amount in cents to avoid floating point issues",
    # )
    # currency = models.CharField(max_length=3, default="usd")
    # description = models.CharField(max_length=500, blank=True)
    # metadata = models.JSONField(default=dict, blank=True)
    # failure_code = models.CharField(max_length=100, blank=True)
    # failure_message = models.TextField(blank=True)

    class Meta:
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        ordering = ["-created_at"]
        # indexes = [
        #     models.Index(fields=["user", "created_at"]),
        #     models.Index(fields=["status", "created_at"]),
        # ]

    def __str__(self) -> str:
        # TODO: Implement
        # return f"{self.transaction_type} - {self.amount_display} ({self.status})"
        return "Transaction"

    # TODO: Implement properties
    # @property
    # def amount_display(self) -> str:
    #     """Format amount for display."""
    #     amount = self.amount_cents / 100
    #     return f"${amount:.2f} {self.currency.upper()}"
    #
    # @property
    # def is_successful(self) -> bool:
    #     """Check if transaction succeeded."""
    #     return self.status == TransactionStatus.SUCCEEDED


class WebhookEvent(BaseModel):
    """
    Stripe webhook event tracking.

    Stores incoming webhook events for:
    - Idempotency (prevent duplicate processing)
    - Debugging and audit trail
    - Retry handling

    Fields:
        stripe_event_id: Unique Stripe event ID (evt_xxx)
        event_type: Stripe event type (e.g., invoice.paid)
        processing_status: Current processing status
        payload: Full event payload (JSON)
        processed_at: When event was successfully processed
        error_message: Error details if processing failed
        retry_count: Number of processing attempts

    Indexes:
        - stripe_event_id (unique)
        - event_type
        - processing_status

    Usage:
        # Check if event already processed (idempotency)
        if WebhookEvent.objects.filter(stripe_event_id=event_id).exists():
            return  # Already processed

        # Create event record
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id=event_id,
            event_type=event_type,
            payload=payload,
        )
    """

    # TODO: Implement model fields
    # stripe_event_id = models.CharField(
    #     max_length=255,
    #     unique=True,
    #     db_index=True,
    #     help_text="Stripe Event ID (evt_xxx)",
    # )
    # event_type = models.CharField(
    #     max_length=100,
    #     db_index=True,
    #     help_text="Stripe event type (e.g., invoice.paid)",
    # )
    # processing_status = models.CharField(
    #     max_length=20,
    #     choices=WebhookProcessingStatus.choices,
    #     default=WebhookProcessingStatus.PENDING,
    # )
    # payload = models.JSONField(help_text="Full Stripe event payload")
    # processed_at = models.DateTimeField(null=True, blank=True)
    # error_message = models.TextField(blank=True)
    # retry_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "Webhook Event"
        verbose_name_plural = "Webhook Events"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        # TODO: Implement
        # return f"{self.event_type} ({self.processing_status})"
        return "WebhookEvent"

    # TODO: Implement methods
    # def mark_processed(self) -> None:
    #     """Mark event as successfully processed."""
    #     from django.utils import timezone
    #     self.processing_status = WebhookProcessingStatus.PROCESSED
    #     self.processed_at = timezone.now()
    #     self.save(update_fields=["processing_status", "processed_at", "updated_at"])
    #
    # def mark_failed(self, error_message: str) -> None:
    #     """Mark event as failed with error message."""
    #     self.processing_status = WebhookProcessingStatus.FAILED
    #     self.error_message = error_message
    #     self.retry_count += 1
    #     self.save(update_fields=["processing_status", "error_message", "retry_count", "updated_at"])
