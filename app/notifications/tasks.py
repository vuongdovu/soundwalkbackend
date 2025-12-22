"""
Celery tasks for notification delivery.

This module contains asynchronous tasks for delivering notifications
through various channels (push, email, WebSocket).

Tasks:
    send_push_notification: Deliver notification via push (FCM/APNS)
    send_email_notification: Deliver notification via email
    broadcast_websocket_notification: Broadcast notification via WebSocket

Design:
    - Tasks receive delivery_id (UUID string) instead of notification_id
    - Each task updates the NotificationDelivery status
    - Permanent vs transient errors are classified for retry logic
    - Tasks are idempotent: re-running on non-PENDING delivery is a no-op

Usage:
    from notifications.tasks import send_push_notification

    # Called automatically by NotificationService.create_notification()
    # Or manually:
    send_push_notification.delay(delivery_id="uuid-string")
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone as django_timezone

from notifications.models import (
    DeliveryStatus,
    NotificationDelivery,
    SkipReason,
)

logger = logging.getLogger(__name__)


# Error classification for retry logic
PERMANENT_ERRORS = {
    "unregistered",
    "invalid_token",
    "invalid_email",
    "hard_bounce",
    "invalid_recipient",
}
TRANSIENT_ERRORS = {
    "rate_limited",
    "timeout",
    "provider_unavailable",
    "connection_error",
}


class DeliveryError(Exception):
    """Base exception for delivery failures."""

    def __init__(self, message: str, code: str, is_permanent: bool = False):
        super().__init__(message)
        self.code = code
        self.is_permanent = is_permanent


def _get_delivery(delivery_id: str) -> NotificationDelivery | None:
    """
    Fetch delivery with related notification.

    Returns None if delivery not found or not in PENDING status.
    """
    try:
        delivery = NotificationDelivery.objects.select_related(
            "notification",
            "notification__recipient",
            "notification__notification_type",
        ).get(id=delivery_id)

        if delivery.status != DeliveryStatus.PENDING:
            logger.info(f"Delivery {delivery_id} status is {delivery.status}, skipping")
            return None

        return delivery
    except NotificationDelivery.DoesNotExist:
        logger.warning(f"Delivery {delivery_id} not found")
        return None


def _mark_sent(
    delivery: NotificationDelivery,
    provider_message_id: str | None = None,
) -> None:
    """Mark delivery as sent (awaiting confirmation)."""
    delivery.status = DeliveryStatus.SENT
    delivery.sent_at = django_timezone.now()
    delivery.provider_message_id = provider_message_id
    delivery.attempt_count += 1
    delivery.save(
        update_fields=[
            "status",
            "sent_at",
            "provider_message_id",
            "attempt_count",
            "updated_at",
        ]
    )


def _mark_delivered(delivery: NotificationDelivery) -> None:
    """Mark delivery as delivered (confirmed)."""
    delivery.status = DeliveryStatus.DELIVERED
    delivery.delivered_at = django_timezone.now()
    delivery.save(update_fields=["status", "delivered_at", "updated_at"])


def _mark_failed(
    delivery: NotificationDelivery,
    error: DeliveryError,
) -> None:
    """Mark delivery as failed."""
    delivery.status = DeliveryStatus.FAILED
    delivery.failed_at = django_timezone.now()
    delivery.failure_reason = str(error)
    delivery.failure_code = error.code
    delivery.is_permanent_failure = error.is_permanent
    delivery.attempt_count += 1
    delivery.save(
        update_fields=[
            "status",
            "failed_at",
            "failure_reason",
            "failure_code",
            "is_permanent_failure",
            "attempt_count",
            "updated_at",
        ]
    )


def _mark_skipped(
    delivery: NotificationDelivery,
    reason: str,
) -> None:
    """Mark delivery as skipped."""
    delivery.status = DeliveryStatus.SKIPPED
    delivery.skipped_reason = reason
    delivery.save(update_fields=["status", "skipped_reason", "updated_at"])


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_push_notification(self, delivery_id: str) -> bool:
    """
    Send notification via push (FCM/APNS).

    Flow:
        1. Fetch delivery + notification
        2. Skip if status != PENDING
        3. Increment attempt_count
        4. Call provider (stub for now)
        5. On success: status=SENT, provider_message_id=X
        6. On permanent error: status=FAILED, is_permanent_failure=True
        7. On transient error: raise for retry

    Args:
        delivery_id: UUID string of the NotificationDelivery

    Returns:
        True if sent successfully or skipped

    Raises:
        DeliveryError: On transient failure (triggers retry)
    """
    delivery = _get_delivery(delivery_id)
    if delivery is None:
        return True  # Already processed or not found

    notification = delivery.notification
    recipient = notification.recipient

    logger.info(
        f"Sending push notification for delivery {delivery_id} to user {recipient.id}"
    )

    try:
        # TODO: Implement actual FCM/APNS integration
        # Steps:
        # 1. Fetch user's device tokens from DeviceToken model
        # 2. Format payload for FCM/APNS
        # 3. Send to appropriate provider based on device platform
        # 4. Handle delivery failures and token invalidation

        # Stub: Simulate successful send
        provider_message_id = f"stub-push-{delivery_id}"
        _mark_sent(delivery, provider_message_id)

        logger.info(
            f"Push notification sent for delivery {delivery_id}, "
            f"provider_message_id={provider_message_id}"
        )
        return True

    except DeliveryError as e:
        if e.is_permanent:
            _mark_failed(delivery, e)
            logger.warning(
                f"Push notification permanently failed for delivery {delivery_id}: "
                f"{e.code} - {e}"
            )
            return False
        else:
            # Increment attempt count before raising for retry
            delivery.attempt_count += 1
            delivery.save(update_fields=["attempt_count", "updated_at"])
            logger.warning(
                f"Push notification transiently failed for delivery {delivery_id}: "
                f"{e.code} - {e}, will retry"
            )
            raise

    except Exception as e:
        # Unexpected error - treat as transient for retry
        delivery.attempt_count += 1
        delivery.save(update_fields=["attempt_count", "updated_at"])
        logger.exception(
            f"Unexpected error sending push for delivery {delivery_id}: {e}"
        )
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_email_notification(self, delivery_id: str) -> bool:
    """
    Send notification via email.

    Flow:
        1. Fetch delivery + notification
        2. Skip if status != PENDING or no email on recipient
        3. Render email template
        4. Send via email provider
        5. Update delivery status

    Args:
        delivery_id: UUID string of the NotificationDelivery

    Returns:
        True if sent successfully or skipped

    Raises:
        DeliveryError: On transient failure (triggers retry)
    """
    delivery = _get_delivery(delivery_id)
    if delivery is None:
        return True  # Already processed or not found

    notification = delivery.notification
    recipient = notification.recipient

    # Check if recipient has email
    if not recipient.email:
        _mark_skipped(delivery, SkipReason.NO_EMAIL)
        logger.info(
            f"Email notification skipped for delivery {delivery_id}: "
            "recipient has no email"
        )
        return True

    logger.info(
        f"Sending email notification for delivery {delivery_id} to {recipient.email}"
    )

    try:
        # TODO: Implement actual email integration
        # Steps:
        # 1. Render email template with notification data
        # 2. Send via configured email backend (SendGrid, Mailgun, etc.)
        # 3. Store provider message ID for webhook correlation

        # Stub: Simulate successful send
        provider_message_id = f"stub-email-{delivery_id}"
        _mark_sent(delivery, provider_message_id)

        logger.info(
            f"Email notification sent for delivery {delivery_id}, "
            f"provider_message_id={provider_message_id}"
        )
        return True

    except DeliveryError as e:
        if e.is_permanent:
            _mark_failed(delivery, e)
            logger.warning(
                f"Email notification permanently failed for delivery {delivery_id}: "
                f"{e.code} - {e}"
            )
            return False
        else:
            delivery.attempt_count += 1
            delivery.save(update_fields=["attempt_count", "updated_at"])
            logger.warning(
                f"Email notification transiently failed for delivery {delivery_id}: "
                f"{e.code} - {e}, will retry"
            )
            raise

    except Exception as e:
        delivery.attempt_count += 1
        delivery.save(update_fields=["attempt_count", "updated_at"])
        logger.exception(
            f"Unexpected error sending email for delivery {delivery_id}: {e}"
        )
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def broadcast_websocket_notification(self, delivery_id: str) -> bool:
    """
    Broadcast notification via WebSocket using Django Channels.

    Unlike push/email, WebSocket delivery is immediate - if the user is
    connected, they receive it; if not, we mark as SKIPPED.

    Flow:
        1. Fetch delivery + notification
        2. Skip if status != PENDING
        3. Check user presence (optional - from chat app)
        4. Broadcast to user's channel group
        5. Mark as DELIVERED (immediate confirmation for WebSocket)

    Args:
        delivery_id: UUID string of the NotificationDelivery

    Returns:
        True if broadcast successfully or skipped

    Raises:
        DeliveryError: On failure (triggers retry)
    """
    delivery = _get_delivery(delivery_id)
    if delivery is None:
        return True  # Already processed or not found

    notification = delivery.notification
    recipient = notification.recipient

    logger.info(
        f"Broadcasting websocket notification for delivery {delivery_id} "
        f"to user {recipient.id}"
    )

    try:
        # TODO: Implement actual Channels integration
        # Steps:
        # 1. Check if user has active connections (PresenceService from chat)
        # 2. If no connections, mark as SKIPPED with NO_CONNECTIONS reason
        # 3. Serialize notification for WebSocket payload
        # 4. Get recipient's channel group name (e.g., f"user_{recipient.id}_notifications")
        # 5. Broadcast to user's notification channel
        # 6. Track devices targeted vs reached

        # Stub: Simulate successful broadcast
        # In reality, check presence first
        has_connections = True  # Stub: assume user is connected

        if not has_connections:
            _mark_skipped(delivery, SkipReason.NO_CONNECTIONS)
            logger.info(
                f"WebSocket notification skipped for delivery {delivery_id}: "
                "no active connections"
            )
            return True

        # For WebSocket, we mark as DELIVERED immediately since it's synchronous
        delivery.status = DeliveryStatus.DELIVERED
        delivery.sent_at = django_timezone.now()
        delivery.delivered_at = django_timezone.now()
        delivery.attempt_count += 1
        delivery.websocket_devices_targeted = 1  # Stub value
        delivery.websocket_devices_reached = 1  # Stub value
        delivery.save(
            update_fields=[
                "status",
                "sent_at",
                "delivered_at",
                "attempt_count",
                "websocket_devices_targeted",
                "websocket_devices_reached",
                "updated_at",
            ]
        )

        logger.info(f"WebSocket notification broadcast for delivery {delivery_id}")
        return True

    except Exception as e:
        delivery.attempt_count += 1
        delivery.save(update_fields=["attempt_count", "updated_at"])
        logger.exception(
            f"Unexpected error broadcasting websocket for delivery {delivery_id}: {e}"
        )
        raise
