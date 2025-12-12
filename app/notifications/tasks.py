"""
Celery tasks for notification delivery.

This module contains asynchronous tasks for delivering notifications
through various channels (push, email, WebSocket).

Tasks:
    send_push_notification: Deliver notification via push (FCM/APNS)
    send_email_notification: Deliver notification via email
    broadcast_websocket_notification: Broadcast notification via WebSocket

Note:
    These are stub implementations. Each task logs the call and returns True.
    Implement the actual delivery logic when integrating with:
    - Firebase Cloud Messaging (FCM) / Apple Push Notification service (APNS)
    - Email provider (SendGrid, Mailgun, etc.)
    - Django Channels for WebSocket

Usage:
    from notifications.tasks import send_push_notification

    # Called automatically by NotificationService.create_notification()
    # Or manually:
    send_push_notification.delay(notification_id=123)
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_push_notification(self, notification_id: int) -> bool:
    """
    Send notification via push (FCM/APNS).

    This is a stub implementation that logs the call.
    Replace with actual FCM/APNS integration.

    Args:
        notification_id: ID of the Notification to send

    Returns:
        True if successful (or stub mode)

    TODO:
        - Fetch user's device tokens from DeviceToken model
        - Format payload for FCM/APNS
        - Send to appropriate provider based on device platform
        - Handle delivery failures and token invalidation
    """
    logger.info(
        f"send_push_notification called for notification_id={notification_id} "
        "(stub implementation)"
    )
    return True


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_email_notification(self, notification_id: int) -> bool:
    """
    Send notification via email.

    This is a stub implementation that logs the call.
    Replace with actual email integration.

    Args:
        notification_id: ID of the Notification to send

    Returns:
        True if successful (or stub mode)

    TODO:
        - Fetch notification and recipient details
        - Render email template with notification data
        - Send via configured email backend
        - Handle bounce/complaint notifications
    """
    logger.info(
        f"send_email_notification called for notification_id={notification_id} "
        "(stub implementation)"
    )
    return True


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def broadcast_websocket_notification(self, notification_id: int) -> bool:
    """
    Broadcast notification via WebSocket using Django Channels.

    This is a stub implementation that logs the call.
    Replace with actual Channels integration.

    Args:
        notification_id: ID of the Notification to broadcast

    Returns:
        True if successful (or stub mode)

    TODO:
        - Fetch notification with related data
        - Serialize notification for WebSocket payload
        - Get recipient's channel group name
        - Broadcast to user's notification channel
    """
    logger.info(
        f"broadcast_websocket_notification called for notification_id={notification_id} "
        "(stub implementation)"
    )
    return True
