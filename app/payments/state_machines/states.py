"""
State enums for payment models.

This module defines all state enums used by payment models with django-fsm.
These are Django TextChoices for database storage and admin integration.

State Machines Overview:

PaymentOrder States:
    draft → pending → processing → captured → settled (direct path)
    draft → pending → processing → held → released → settled (escrow path)
    draft/pending → cancelled
    processing → failed → pending (retry)
    captured/settled → refunded/partially_refunded

Payout States:
    pending → scheduled → processing → paid
    pending/scheduled → processing → failed → pending (retry)

Refund States:
    requested → processing → completed
    requested → processing → failed
"""

from django.db import models


class PaymentOrderState(models.TextChoices):
    """
    States for the PaymentOrder model lifecycle.

    Terminal states: SETTLED, FAILED, CANCELLED, REFUNDED
    Non-terminal states can transition to other states.

    State Flow (Direct Payment):
        DRAFT → PENDING → PROCESSING → CAPTURED → SETTLED

    State Flow (Escrow Payment):
        DRAFT → PENDING → PROCESSING → CAPTURED → HELD → RELEASED → SETTLED

    Recovery Flow:
        FAILED → PENDING (retry)

    Cancellation Flow:
        DRAFT → CANCELLED
        PENDING → CANCELLED

    Refund Flow:
        CAPTURED → REFUNDED / PARTIALLY_REFUNDED
        SETTLED → REFUNDED / PARTIALLY_REFUNDED
    """

    DRAFT = "draft", "Draft"
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    CAPTURED = "captured", "Captured"
    HELD = "held", "Held"
    RELEASED = "released", "Released"
    SETTLED = "settled", "Settled"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    REFUNDED = "refunded", "Refunded"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially Refunded"


class PayoutState(models.TextChoices):
    """
    States for the Payout model lifecycle.

    Terminal states: PAID, FAILED (but can retry from FAILED), CANCELLED

    State Flow:
        PENDING → SCHEDULED → PROCESSING → PAID
        PENDING → PROCESSING → PAID (immediate)
        PROCESSING → FAILED → PENDING (retry)
        PENDING/SCHEDULED → CANCELLED (for refund processing)
    """

    PENDING = "pending", "Pending"
    SCHEDULED = "scheduled", "Scheduled"
    PROCESSING = "processing", "Processing"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class RefundState(models.TextChoices):
    """
    States for the Refund model lifecycle.

    Terminal states: COMPLETED, FAILED

    State Flow:
        REQUESTED → PROCESSING → COMPLETED
        REQUESTED → PROCESSING → FAILED
    """

    REQUESTED = "requested", "Requested"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class OnboardingStatus(models.TextChoices):
    """
    Stripe Connect onboarding status for ConnectedAccount.

    Reflects the state of the merchant's Stripe Connect onboarding process.
    Only COMPLETE status allows receiving payouts.
    """

    NOT_STARTED = "not_started", "Not Started"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETE = "complete", "Complete"
    REJECTED = "rejected", "Rejected"


class WebhookEventStatus(models.TextChoices):
    """
    Processing status for WebhookEvent.

    Tracks the lifecycle of webhook event processing for idempotency.

    State Flow:
        PENDING → PROCESSING → PROCESSED
        PENDING → PROCESSING → FAILED (can retry)
    """

    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class PaymentStrategyType(models.TextChoices):
    """
    Payment processing strategy types.

    Determines how a payment flows through the system:
    - DIRECT: Immediate capture and settlement
    - ESCROW: Hold funds until service completion
    - SUBSCRIPTION: Recurring payments via Stripe Subscription
    """

    DIRECT = "direct", "Direct Payment"
    ESCROW = "escrow", "Escrow Payment"
    SUBSCRIPTION = "subscription", "Subscription"


__all__ = [
    "PaymentOrderState",
    "PayoutState",
    "RefundState",
    "OnboardingStatus",
    "WebhookEventStatus",
    "PaymentStrategyType",
]
