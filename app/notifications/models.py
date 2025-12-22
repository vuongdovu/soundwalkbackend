"""
Notification system models.

This module defines the core models for the notification system:
- NotificationType: Configuration for notification types with templates
- Notification: Individual notifications sent to users
- UserGlobalPreference: Global notification mute setting per user
- UserCategoryPreference: Category-level notification preferences
- UserNotificationPreference: Type-level notification preferences
- NotificationDelivery: Per-channel delivery tracking

Design Decisions:
    - NotificationType uses integer PK (internal lookup table)
    - Notification inherits from BaseModel (timestamps, ordering)
    - Actor uses SET_NULL (preserve notification when actor deleted)
    - NotificationType uses PROTECT (prevent deletion with existing notifications)
    - GenericForeignKey for linking to any source object
    - Preference hierarchy: Global -> Category -> Type -> Channel
    - Delivery records track status per channel for retry/analytics

Usage:
    from notifications.models import NotificationType, Notification

    # Create a notification type
    nt = NotificationType.objects.create(
        key="new_follower",
        display_name="New Follower",
        title_template="{actor_name} started following you",
        category=NotificationCategory.SOCIAL,
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


# =============================================================================
# Enums
# =============================================================================


class NotificationCategory(models.TextChoices):
    """
    Categories for grouping notification types.

    Used for category-level preference overrides. Users can disable
    entire categories (e.g., all marketing notifications).
    """

    TRANSACTIONAL = "transactional", "Transactional"
    SOCIAL = "social", "Social"
    MARKETING = "marketing", "Marketing"
    SYSTEM = "system", "System"


class DeliveryChannel(models.TextChoices):
    """Delivery channels for notifications."""

    PUSH = "push", "Push Notification"
    EMAIL = "email", "Email"
    WEBSOCKET = "websocket", "WebSocket"


class DeliveryStatus(models.TextChoices):
    """
    Status of a notification delivery attempt.

    State Flow:
        PENDING -> SENT -> DELIVERED (via webhook confirmation)
        PENDING -> FAILED (permanent error or retries exhausted)
        SKIPPED (user preference disabled or no delivery target)
    """

    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class SkipReason(models.TextChoices):
    """Standardized reasons for skipped deliveries."""

    GLOBAL_DISABLED = "global_disabled", "Global notifications disabled"
    CATEGORY_DISABLED = "category_disabled", "Category disabled"
    TYPE_DISABLED = "type_disabled", "Type disabled"
    CHANNEL_DISABLED = "channel_disabled", "Channel disabled by user"
    NO_DEVICE_TOKEN = "no_device_token", "No device token"
    NO_EMAIL = "no_email", "No email address"
    NO_CONNECTIONS = "no_connections", "No active connections"


# =============================================================================
# Configuration Models
# =============================================================================


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

    category = models.CharField(
        max_length=20,
        choices=NotificationCategory.choices,
        default=NotificationCategory.TRANSACTIONAL,
        db_index=True,
        help_text="Category for preference grouping",
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

    idempotency_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Idempotency key to prevent duplicate notifications",
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
            # Idempotency key lookup (partial index for non-null keys only)
            models.Index(
                fields=["idempotency_key"],
                name="notif_idempotency_key_idx",
                condition=models.Q(idempotency_key__isnull=False),
            ),
        ]
        constraints = [
            # Unique constraint on idempotency_key when not null
            models.UniqueConstraint(
                fields=["idempotency_key"],
                name="notif_idempotency_key_unique",
                condition=models.Q(idempotency_key__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        read_status = "read" if self.is_read else "unread"
        return (
            f"Notification({self.notification_type.key}) -> "
            f"User {self.recipient_id} [{read_status}]"
        )


# =============================================================================
# Preference Models
# =============================================================================


class UserGlobalPreference(BaseModel):
    """
    Global notification preferences for a user.

    One-to-One with User. If all_disabled is True, all notifications
    are suppressed regardless of other preference settings.

    Usage:
        # Disable all notifications for a user
        pref, _ = UserGlobalPreference.objects.get_or_create(user=user)
        pref.all_disabled = True
        pref.save()
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="notification_global_preference",
    )

    all_disabled = models.BooleanField(
        default=False,
        help_text="Master switch to disable all notifications",
    )

    class Meta:
        db_table = "notifications_user_global_preference"
        verbose_name = "user global preference"
        verbose_name_plural = "user global preferences"

    def __str__(self) -> str:
        status = "disabled" if self.all_disabled else "enabled"
        return f"GlobalPreference(user={self.user_id}, {status})"


class UserCategoryPreference(BaseModel):
    """
    Category-level notification preferences.

    Allows users to disable entire categories (e.g., all marketing).
    Takes precedence over type-level preferences when disabled.

    Usage:
        # Disable marketing notifications for a user
        UserCategoryPreference.objects.update_or_create(
            user=user,
            category=NotificationCategory.MARKETING,
            defaults={"disabled": True},
        )
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_category_preferences",
    )

    category = models.CharField(
        max_length=20,
        choices=NotificationCategory.choices,
        help_text="Notification category",
    )

    disabled = models.BooleanField(
        default=False,
        help_text="Disable all notifications in this category",
    )

    class Meta:
        db_table = "notifications_user_category_preference"
        verbose_name = "user category preference"
        verbose_name_plural = "user category preferences"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "category"],
                name="unique_user_category_pref",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "category"],
                name="notif_cat_pref_user_cat_idx",
            ),
        ]

    def __str__(self) -> str:
        status = "disabled" if self.disabled else "enabled"
        return f"CategoryPreference(user={self.user_id}, {self.category}={status})"


class UserNotificationPreference(BaseModel):
    """
    Per-notification-type preferences for a user.

    Allows granular control over individual notification types and channels.
    Null values for channel fields mean "inherit from NotificationType default".

    Usage:
        # Disable email for a specific notification type
        UserNotificationPreference.objects.update_or_create(
            user=user,
            notification_type=notification_type,
            defaults={"email_enabled": False},
        )
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_type_preferences",
    )

    notification_type = models.ForeignKey(
        NotificationType,
        on_delete=models.CASCADE,
        related_name="user_preferences",
    )

    # Master kill switch for this type
    disabled = models.BooleanField(
        default=False,
        help_text="Disable all channels for this notification type",
    )

    # Per-channel overrides (null = inherit from NotificationType)
    push_enabled = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="Override push preference (null = use type default)",
    )

    email_enabled = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="Override email preference (null = use type default)",
    )

    websocket_enabled = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="Override websocket preference (null = use type default)",
    )

    class Meta:
        db_table = "notifications_user_notification_preference"
        verbose_name = "user notification preference"
        verbose_name_plural = "user notification preferences"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "notification_type"],
                name="unique_user_notif_type_pref",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "notification_type"],
                name="notif_type_pref_user_type_idx",
            ),
        ]

    def __str__(self) -> str:
        status = "disabled" if self.disabled else "enabled"
        return f"TypePreference(user={self.user_id}, type={self.notification_type_id}, {status})"


# =============================================================================
# Delivery Tracking Models
# =============================================================================


class NotificationDelivery(BaseModel):
    """
    Tracks delivery status for each channel of a notification.

    One NotificationDelivery record per (notification, channel) combination.
    This enables retry logic, delivery confirmation via webhooks, and analytics.

    Fields:
        notification: The notification being delivered
        channel: Delivery channel (push, email, websocket)
        status: Current delivery status
        attempt_count: Number of delivery attempts
        provider_message_id: External ID from provider (for webhook callbacks)
        skipped_reason: Why delivery was skipped (if status=SKIPPED)
        failure_reason: Detailed error message if failed
        is_permanent_failure: Whether failure is permanent (no retry)

    Usage:
        # Create pending delivery
        delivery = NotificationDelivery.objects.create(
            notification=notification,
            channel=DeliveryChannel.PUSH,
            status=DeliveryStatus.PENDING,
        )

        # Update after sending
        delivery.status = DeliveryStatus.SENT
        delivery.sent_at = timezone.now()
        delivery.provider_message_id = "fcm_msg_123"
        delivery.save()
    """

    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )

    channel = models.CharField(
        max_length=20,
        choices=DeliveryChannel.choices,
        help_text="Delivery channel",
    )

    status = models.CharField(
        max_length=20,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
        db_index=True,
        help_text="Current delivery status",
    )

    # Timestamps
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the notification was sent to the provider",
    )

    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When delivery was confirmed (via webhook)",
    )

    failed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When delivery failed",
    )

    # Provider tracking
    provider_message_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text="Message ID from provider (FCM, SES, etc.) for webhook lookup",
    )

    # Failure details
    failure_reason = models.TextField(
        blank=True,
        default="",
        help_text="Detailed failure message",
    )

    failure_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Error code from provider",
    )

    is_permanent_failure = models.BooleanField(
        default=False,
        help_text="True if retry won't help (e.g., invalid token)",
    )

    attempt_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of delivery attempts",
    )

    # Skip tracking
    skipped_reason = models.CharField(
        max_length=30,
        choices=SkipReason.choices,
        blank=True,
        default="",
        help_text="Reason if status=SKIPPED",
    )

    # WebSocket-specific metrics
    websocket_devices_targeted = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of WebSocket connections targeted",
    )

    websocket_devices_reached = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of WebSocket connections that received the message",
    )

    class Meta:
        db_table = "notifications_notification_delivery"
        verbose_name = "notification delivery"
        verbose_name_plural = "notification deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "channel"],
                name="unique_notification_channel",
            ),
        ]
        indexes = [
            # For retry queries: find pending/failed deliveries by channel
            models.Index(
                fields=["status", "channel", "-created_at"],
                name="notif_delivery_status_idx",
            ),
            # For webhook lookup by provider message ID
            models.Index(
                fields=["provider_message_id"],
                name="notif_delivery_provider_idx",
                condition=models.Q(provider_message_id__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        return f"Delivery({self.notification_id}, {self.channel}, {self.status})"
