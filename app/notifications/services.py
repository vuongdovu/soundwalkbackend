"""
Notification service layer.

This module provides the business logic for the notification system,
encapsulating all operations for creating and managing notifications.

Services:
    NotificationService: Notification creation and read status management

Design Principles:
    - Services are stateless (use class methods)
    - Expected failures return ServiceResult.failure()
    - Template rendering raises KeyError on missing placeholders
    - Celery tasks are enqueued based on notification type channel support

Usage:
    from notifications.services import NotificationService

    # Create a notification with template rendering
    result = NotificationService.create_notification(
        recipient=user,
        type_key="new_follower",
        data={"actor_name": "John Doe"},
        actor=john_doe,
    )

    # Create with explicit title/body (overrides template)
    result = NotificationService.create_notification(
        recipient=user,
        type_key="system_alert",
        title="Maintenance Notice",
        body="System will be down for maintenance.",
    )

    # Mark as read
    result = NotificationService.mark_as_read(notification, user)

    # Mark all as read
    result = NotificationService.mark_all_as_read(user)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from core.services import BaseService, ServiceResult

from notifications.models import Notification, NotificationType

if TYPE_CHECKING:
    from django.db.models import Model

    from authentication.models import User

logger = logging.getLogger(__name__)


class NotificationService(BaseService):
    """
    Service for notification operations.

    Methods:
        create_notification: Create a new notification with template rendering
        mark_as_read: Mark a single notification as read
        mark_all_as_read: Mark all user's unread notifications as read
    """

    @classmethod
    def create_notification(
        cls,
        recipient: User,
        type_key: str,
        data: dict | None = None,
        title: str | None = None,
        body: str | None = None,
        actor: User | None = None,
        source_object: Model | None = None,
    ) -> ServiceResult[Notification]:
        """
        Create a new notification for a user.

        If title/body are not provided, templates from NotificationType are
        rendered using the data dict. Explicit title/body override templates.

        Implementation:
            1. Look up NotificationType by key
            2. Validate type exists and is active
            3. Render templates (or use explicit values)
            4. Create notification in transaction
            5. Enqueue Celery tasks for supported channels

        Args:
            recipient: User receiving the notification
            type_key: NotificationType.key to look up
            data: Dict for template rendering (e.g., {"actor_name": "John"})
            title: Explicit title (overrides template)
            body: Explicit body (overrides template)
            actor: User who triggered the notification (optional)
            source_object: Object that triggered the notification (GFK)

        Returns:
            ServiceResult with created Notification if successful

        Error codes:
            TYPE_NOT_FOUND: Notification type key doesn't exist
            TYPE_INACTIVE: Notification type is deactivated

        Raises:
            KeyError: If template placeholder is missing from data

        Example:
            # With template
            result = NotificationService.create_notification(
                recipient=user,
                type_key="new_follower",
                data={"actor_name": follower.display_name},
                actor=follower,
            )

            # With explicit title/body
            result = NotificationService.create_notification(
                recipient=user,
                type_key="system_alert",
                title="Server Maintenance",
                body="Scheduled maintenance at midnight.",
            )
        """
        # Import tasks here to avoid circular imports
        from notifications import tasks

        data = data or {}

        # Look up notification type
        try:
            notification_type = NotificationType.objects.get(key=type_key)
        except NotificationType.DoesNotExist:
            cls.get_logger().warning(f"Notification type not found: {type_key}")
            return ServiceResult.failure(
                f"Notification type not found: {type_key}",
                error_code="TYPE_NOT_FOUND",
            )

        # Check if type is active
        if not notification_type.is_active:
            cls.get_logger().info(
                f"Notification type inactive: {type_key} - skipping creation"
            )
            return ServiceResult.failure(
                f"Notification type is inactive: {type_key}",
                error_code="TYPE_INACTIVE",
            )

        # Render templates or use explicit values
        # KeyError is raised if placeholder is missing - this is intentional
        rendered_title = title or notification_type.title_template.format(**data)
        rendered_body = body or notification_type.body_template.format(**data)

        # Prepare GFK fields if source_object provided
        content_type = None
        object_id = None
        if source_object is not None:
            content_type = ContentType.objects.get_for_model(source_object)
            # Convert PK to string to support both UUID and integer PKs
            object_id = str(source_object.pk)

        # Create notification in transaction
        with transaction.atomic():
            notification = Notification.objects.create(
                notification_type=notification_type,
                recipient=recipient,
                actor=actor,
                title=rendered_title,
                body=rendered_body,
                data=data,
                content_type=content_type,
                object_id=object_id,
            )

        cls.get_logger().info(
            f"Created notification {notification.id} of type {type_key} "
            f"for user {recipient.id}"
        )

        # Enqueue delivery tasks based on channel support
        if notification_type.supports_push:
            tasks.send_push_notification.delay(notification.id)

        if notification_type.supports_email:
            tasks.send_email_notification.delay(notification.id)

        if notification_type.supports_websocket:
            tasks.broadcast_websocket_notification.delay(notification.id)

        return ServiceResult.success(notification)

    @classmethod
    def mark_as_read(
        cls,
        notification: Notification,
        user: User,
    ) -> ServiceResult[Notification]:
        """
        Mark a single notification as read.

        Validates that the user owns the notification before marking.
        Operation is idempotent - marking an already-read notification succeeds.

        Args:
            notification: The notification to mark as read
            user: The user making the request (for ownership validation)

        Returns:
            ServiceResult with updated Notification if successful

        Error codes:
            NOT_OWNER: User doesn't own the notification
        """
        # Validate ownership
        if notification.recipient_id != user.id:
            cls.get_logger().warning(
                f"User {user.id} attempted to mark notification {notification.id} "
                f"owned by user {notification.recipient_id}"
            )
            return ServiceResult.failure(
                "Cannot mark notification you don't own",
                error_code="NOT_OWNER",
            )

        # Mark as read (idempotent)
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read", "updated_at"])
            cls.get_logger().debug(f"Marked notification {notification.id} as read")

        return ServiceResult.success(notification)

    @classmethod
    def mark_all_as_read(cls, user: User) -> ServiceResult[int]:
        """
        Mark all user's unread notifications as read.

        Performs a bulk update in a single database query for efficiency.

        Args:
            user: The user whose notifications to mark as read

        Returns:
            ServiceResult with count of notifications marked as read
        """
        count = Notification.objects.filter(
            recipient=user,
            is_read=False,
        ).update(is_read=True)

        cls.get_logger().info(
            f"Marked {count} notifications as read for user {user.id}"
        )

        return ServiceResult.success(count)
