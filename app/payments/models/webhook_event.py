"""
WebhookEvent model for Stripe webhook event tracking.

Stores every webhook event received from Stripe for idempotent
processing and audit trails. The unique stripe_event_id constraint
ensures duplicate webhooks are detected and handled correctly.

Usage:
    from payments.models import WebhookEvent
    from payments.state_machines import WebhookEventStatus

    # Store incoming webhook event
    event, created = WebhookEvent.objects.get_or_create(
        stripe_event_id="evt_1234567890",
        defaults={
            "event_type": "payment_intent.succeeded",
            "payload": webhook_payload,
        }
    )

    if not created and event.status == WebhookEventStatus.PROCESSED:
        # Duplicate webhook - already processed
        return HttpResponse(status=200)

    # Process the event
    event.status = WebhookEventStatus.PROCESSING
    event.save()
    # ... handle event ...
    event.status = WebhookEventStatus.PROCESSED
    event.processed_at = timezone.now()
    event.save()
"""

from __future__ import annotations

from django.db import models

from core.models import BaseModel
from core.model_mixins import UUIDPrimaryKeyMixin

from payments.state_machines import WebhookEventStatus


class WebhookEvent(UUIDPrimaryKeyMixin, BaseModel):
    """
    Tracks Stripe webhook events for idempotent processing.

    Stores the full webhook payload and processing status to:
    1. Prevent duplicate handling (idempotency)
    2. Enable retry logic for failed events
    3. Provide audit trail for debugging

    Processing Flow:
        1. Webhook arrives, verify Stripe signature
        2. Insert/get WebhookEvent with stripe_event_id
        3. If exists and PROCESSED -> return 200 (duplicate)
        4. If exists and PROCESSING -> return 200 (in progress)
        5. Set status to PROCESSING
        6. Route to appropriate handler
        7. Set status to PROCESSED or FAILED
        8. If FAILED, retry worker will pick up later

    Fields:
        stripe_event_id: Unique Stripe Event ID (evt_xxx)
        event_type: Type of webhook event
        payload: Full JSON payload from Stripe
        status: Processing status
        processed_at: When event was successfully processed
        error_message: Error details if processing failed
        retry_count: Number of processing attempts

    Note:
        No version field needed - webhooks are processed once.
        Idempotency is enforced via stripe_event_id unique constraint.
    """

    # ==========================================================================
    # Event Identification
    # ==========================================================================

    stripe_event_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Stripe Event ID (evt_xxx) - unique constraint for idempotency",
    )

    event_type = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Stripe event type (e.g., 'payment_intent.succeeded')",
    )

    # ==========================================================================
    # Payload
    # ==========================================================================

    payload = models.JSONField(
        help_text="Full webhook payload from Stripe (JSON)",
    )

    # ==========================================================================
    # Processing Status
    # ==========================================================================

    status = models.CharField(
        max_length=20,
        choices=WebhookEventStatus.choices,
        default=WebhookEventStatus.PENDING,
        db_index=True,
        help_text="Current processing status",
    )

    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When event was successfully processed",
    )

    # ==========================================================================
    # Error Handling
    # ==========================================================================

    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Error message if processing failed",
    )

    retry_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of processing attempts",
    )

    # ==========================================================================
    # Meta & Methods
    # ==========================================================================

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Webhook Event"
        verbose_name_plural = "Webhook Events"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["status", "retry_count"]),
        ]

    def __str__(self) -> str:
        """Return string representation with event ID and type."""
        return f"WebhookEvent({self.stripe_event_id}, {self.event_type})"

    # ==========================================================================
    # Properties
    # ==========================================================================

    @property
    def is_processed(self) -> bool:
        """Check if event has been successfully processed."""
        return self.status == WebhookEventStatus.PROCESSED

    @property
    def is_pending(self) -> bool:
        """Check if event is pending processing."""
        return self.status == WebhookEventStatus.PENDING

    @property
    def is_failed(self) -> bool:
        """Check if event processing failed."""
        return self.status == WebhookEventStatus.FAILED

    @property
    def can_retry(self) -> bool:
        """Check if event can be retried (failed with retry count < max)."""
        max_retries = 5  # Could be configurable via settings
        return self.is_failed and self.retry_count < max_retries

    # ==========================================================================
    # Helper Methods
    # ==========================================================================

    def mark_processing(self) -> None:
        """
        Mark event as being processed.

        Note: Does not save - caller must save after calling.
        """
        self.status = WebhookEventStatus.PROCESSING
        self.retry_count += 1

    def mark_processed(self) -> None:
        """
        Mark event as successfully processed.

        Note: Does not save - caller must save after calling.
        """
        from django.utils import timezone

        self.status = WebhookEventStatus.PROCESSED
        self.processed_at = timezone.now()
        self.error_message = None

    def mark_failed(self, error_message: str) -> None:
        """
        Mark event as failed with error message.

        Args:
            error_message: Description of what went wrong

        Note: Does not save - caller must save after calling.
        """
        self.status = WebhookEventStatus.FAILED
        self.error_message = error_message

    def get_object_id(self) -> str | None:
        """
        Extract the primary object ID from the webhook payload.

        For most Stripe webhooks, the object ID is in payload.data.object.id

        Returns:
            The object ID if found, None otherwise
        """
        try:
            return self.payload.get("data", {}).get("object", {}).get("id")
        except (AttributeError, TypeError):
            return None

    def get_object_type(self) -> str | None:
        """
        Extract the object type from the webhook payload.

        Returns:
            The object type (e.g., 'payment_intent') if found, None otherwise
        """
        try:
            return self.payload.get("data", {}).get("object", {}).get("object")
        except (AttributeError, TypeError):
            return None
