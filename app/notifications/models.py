"""
Notification system models.

This module defines the core models for the notification system:
- NotificationType: Configuration for notification types with templates
- Notification: Individual notifications sent to users

Design Decisions:
    - NotificationType uses integer PK (internal lookup table)
    - Notification inherits from BaseModel (timestamps, ordering)
    - Actor uses SET_NULL (preserve notification when actor deleted)
    - NotificationType uses PROTECT (prevent deletion with existing notifications)
    - GenericForeignKey for linking to any source object

Usage:
    from notifications.models import NotificationType, Notification

    # Create a notification type
    nt = NotificationType.objects.create(
        key="new_follower",
        display_name="New Follower",
        title_template="{actor_name} started following you",
    )

    # Create a notification
    notification = Notification.objects.create(
        notification_type=nt,
        recipient=user,
        title="John Doe started following you",
        actor=john_doe,
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.models import BaseModel

if TYPE_CHECKING:
    pass


class NotificationType(models.Model):
    """
    Lookup table for notification type definitions.

    This is a configuration table for defining notification templates and
    delivery channel support. Typically seeded via data migration.

    Fields:
        key: Unique programmatic identifier (e.g., "order_shipped")
        display_name: Human-readable name for admin/UI display
        title_template: Python format string for notification title
        body_template: Python format string for notification body
        is_active: Whether this notification type is currently enabled
        supports_push: Can be delivered via push notification (FCM/APNS)
        supports_email: Can be delivered via email
        supports_websocket: Can be broadcast in real-time via WebSocket

    Usage:
        # Create with template placeholders
        nt = NotificationType.objects.create(
            key="order_shipped",
            display_name="Order Shipped",
            title_template="Your order {order_id} has shipped",
            body_template="Track your package: {tracking_url}",
        )

        # Render template
        title = nt.title_template.format(order_id="12345")

    Note:
        - Templates use Python str.format() syntax: {placeholder}
        - Missing placeholders raise KeyError during rendering
        - Empty templates return empty string when rendered
    """

    # Auto-increment integer PK (default Django behavior)

    key = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Unique programmatic identifier (e.g., 'order_shipped')",
    )

    display_name = models.CharField(
        max_length=200,
        help_text="Human-readable name for display",
    )

    title_template = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Python format string template for title (e.g., '{actor_name} liked your post')",
    )

    body_template = models.TextField(
        blank=True,
        default="",
        help_text="Python format string template for body",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this notification type is currently enabled",
    )

    # Channel support flags
    supports_push = models.BooleanField(
        default=True,
        help_text="Can be delivered via push notification",
    )

    supports_email = models.BooleanField(
        default=False,
        help_text="Can be delivered via email",
    )

    supports_websocket = models.BooleanField(
        default=True,
        help_text="Can be broadcast via WebSocket",
    )

    class Meta:
        db_table = "notifications_notification_type"
        verbose_name = "notification type"
        verbose_name_plural = "notification types"
        ordering = ["key"]

    def __str__(self) -> str:
        return f"{self.display_name} ({self.key})"


class Notification(BaseModel):
    """
    Individual notification record for a user.

    Notifications are immutable once created - title and body are
    fully rendered strings serving as historical records.

    Fields:
        notification_type: FK to NotificationType (defines behavior)
        recipient: User receiving the notification (scopes all queries)
        actor: Optional user who triggered the notification
        title: Fully rendered title string
        body: Fully rendered body string
        data: Arbitrary JSON context (deep links, metadata)
        content_type/object_id/source_object: Generic FK to source entity
        is_read: Whether recipient has read this notification

    Inherits from BaseModel:
        created_at: Timestamp (auto, indexed)
        updated_at: Timestamp (auto)

    Usage:
        # Create notification
        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="John Doe started following you",
            actor=john_doe,
        )

        # Mark as read
        notification.is_read = True
        notification.save()

        # Query user's unread notifications
        unread = Notification.objects.filter(
            recipient=user,
            is_read=False,
        )

    Note:
        - recipient CASCADE: Notifications deleted when user deleted
        - actor SET_NULL: Notification preserved when actor deleted
        - notification_type PROTECT: Cannot delete type with existing notifications
    """

    notification_type = models.ForeignKey(
        NotificationType,
        on_delete=models.PROTECT,  # Prevent deletion of types with existing notifications
        related_name="notifications",
        help_text="Type of this notification",
    )

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
        help_text="User receiving this notification",
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_notifications",
        help_text="User who triggered this notification (optional)",
    )

    title = models.CharField(
        max_length=500,
        help_text="Fully rendered notification title",
    )

    body = models.TextField(
        blank=True,
        default="",
        help_text="Fully rendered notification body",
    )

    data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary context data (deep links, metadata)",
    )

    # Generic foreign key for linking to source entity
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Content type of source object",
    )

    object_id = models.CharField(
        max_length=36,
        null=True,
        blank=True,
        help_text="ID of source object (supports UUID and integer PKs)",
    )

    source_object = GenericForeignKey("content_type", "object_id")

    is_read = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether recipient has read this notification",
    )

    class Meta:
        db_table = "notifications_notification"
        ordering = ["-created_at"]  # Newest first
        indexes = [
            # Primary query: user's unread notifications
            models.Index(
                fields=["recipient", "is_read", "-created_at"],
                name="notif_recipient_unread_idx",
            ),
            # User's notifications by type
            models.Index(
                fields=["recipient", "notification_type"],
                name="notif_recipient_type_idx",
            ),
        ]

    def __str__(self) -> str:
        read_status = "read" if self.is_read else "unread"
        return (
            f"Notification({self.notification_type.key}) -> "
            f"User {self.recipient_id} [{read_status}]"
        )
