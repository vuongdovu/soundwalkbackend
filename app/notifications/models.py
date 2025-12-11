"""
Notification models.

This module defines models for:
- Notification: Individual notification records
- DeviceToken: Push notification device tokens
- NotificationPreference: User notification settings

Related files:
    - services.py: NotificationService for sending
    - tasks.py: Async delivery tasks
    - handlers.py: Cross-app event handlers

Model Relationships:
    User (1) ---> (*) Notification
    User (1) ---> (*) DeviceToken
    User (1) ---> (1) NotificationPreference
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

from core.models import BaseModel

if TYPE_CHECKING:
    pass


class NotificationType(models.TextChoices):
    """Types of notifications."""

    SYSTEM = "system", "System"
    CHAT = "chat", "Chat Message"
    PAYMENT = "payment", "Payment"
    SUBSCRIPTION = "subscription", "Subscription"
    AI = "ai", "AI Response"


class NotificationChannel(models.TextChoices):
    """Delivery channels for notifications."""

    IN_APP = "in_app", "In-App"
    EMAIL = "email", "Email"
    PUSH = "push", "Push Notification"


class DevicePlatform(models.TextChoices):
    """Mobile device platforms."""

    IOS = "ios", "iOS"
    ANDROID = "android", "Android"
    WEB = "web", "Web"


class Notification(BaseModel):
    """
    Individual notification record.

    Stores all notifications sent to users for display
    and history tracking.

    Fields:
        user: Recipient user
        notification_type: Type of notification
        title: Notification title (max 200 chars)
        body: Notification body text
        channel: Delivery channel used
        is_read: Whether user has read it
        read_at: When notification was read
        action_url: Optional URL for click action
        metadata: Additional data (chat_id, message_id, etc.)
        expires_at: Optional expiration time

    Indexes:
        - [user, is_read, created_at]
        - [user, notification_type, is_read]
        - [expires_at] for cleanup

    Usage:
        notifications = Notification.objects.filter(
            user=user,
            is_read=False
        ).order_by("-created_at")[:10]
    """

    # TODO: Implement model fields
    # user = models.ForeignKey(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.CASCADE,
    #     related_name="notifications",
    # )
    # notification_type = models.CharField(
    #     max_length=20,
    #     choices=NotificationType.choices,
    #     db_index=True,
    # )
    # title = models.CharField(max_length=200)
    # body = models.TextField()
    # channel = models.CharField(
    #     max_length=20,
    #     choices=NotificationChannel.choices,
    # )
    # is_read = models.BooleanField(default=False, db_index=True)
    # read_at = models.DateTimeField(null=True, blank=True)
    # action_url = models.URLField(max_length=500, blank=True)
    # metadata = models.JSONField(
    #     default=dict,
    #     blank=True,
    #     help_text="Additional data: chat_id, message_id, etc.",
    # )
    # expires_at = models.DateTimeField(
    #     null=True,
    #     blank=True,
    #     db_index=True,
    #     help_text="Notification expires after this time",
    # )

    class Meta:
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        ordering = ["-created_at"]
        # indexes = [
        #     models.Index(fields=["user", "is_read", "-created_at"]),
        #     models.Index(fields=["user", "notification_type", "is_read"]),
        # ]

    def __str__(self) -> str:
        # TODO: Implement
        # return f"{self.notification_type}: {self.title[:50]}"
        return "Notification"

    # TODO: Implement methods
    # def mark_as_read(self) -> None:
    #     """Mark notification as read."""
    #     if not self.is_read:
    #         from django.utils import timezone
    #         self.is_read = True
    #         self.read_at = timezone.now()
    #         self.save(update_fields=["is_read", "read_at", "updated_at"])
    #
    # @property
    # def is_expired(self) -> bool:
    #     """Check if notification has expired."""
    #     if not self.expires_at:
    #         return False
    #     from django.utils import timezone
    #     return timezone.now() > self.expires_at


class DeviceToken(BaseModel):
    """
    Push notification device token.

    Stores FCM/APNs tokens for push notification delivery.
    One user can have multiple devices.

    Fields:
        user: Token owner
        token: FCM or APNs token
        platform: Device platform (ios/android/web)
        device_id: Unique device identifier
        is_active: Whether token is valid
        last_used_at: Last successful push
        device_name: Human-readable device name
        app_version: App version for debugging

    Indexes:
        - token (unique)
        - [user, is_active]
        - device_id

    Unique together:
        - [user, device_id]

    Usage:
        tokens = DeviceToken.objects.filter(
            user=user,
            is_active=True
        )
    """

    # TODO: Implement model fields
    # user = models.ForeignKey(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.CASCADE,
    #     related_name="device_tokens",
    # )
    # token = models.TextField(
    #     unique=True,
    #     help_text="FCM or APNs device token",
    # )
    # platform = models.CharField(
    #     max_length=10,
    #     choices=DevicePlatform.choices,
    # )
    # device_id = models.CharField(
    #     max_length=255,
    #     db_index=True,
    #     help_text="Unique device identifier",
    # )
    # is_active = models.BooleanField(default=True, db_index=True)
    # last_used_at = models.DateTimeField(auto_now=True)
    # device_name = models.CharField(
    #     max_length=100,
    #     blank=True,
    #     help_text="Human-readable device name",
    # )
    # app_version = models.CharField(
    #     max_length=20,
    #     blank=True,
    #     help_text="App version for debugging",
    # )

    class Meta:
        verbose_name = "Device Token"
        verbose_name_plural = "Device Tokens"
        # unique_together = [["user", "device_id"]]
        # indexes = [
        #     models.Index(fields=["user", "is_active"]),
        # ]

    def __str__(self) -> str:
        # TODO: Implement
        # return f"{self.user.email} - {self.platform} ({self.device_name or self.device_id[:8]})"
        return "DeviceToken"

    # TODO: Implement methods
    # def deactivate(self) -> None:
    #     """Mark token as inactive (e.g., after push failure)."""
    #     self.is_active = False
    #     self.save(update_fields=["is_active", "updated_at"])


class NotificationPreference(BaseModel):
    """
    User notification preferences.

    Stores per-user settings for notification delivery.
    Controls which channels and types of notifications user receives.

    Fields:
        user: OneToOne link to User (primary key)
        preferences: JSONField with per-type channel settings
        email_enabled: Global email toggle
        push_enabled: Global push toggle
        quiet_hours_start/end: Do not disturb hours

    Preferences JSON structure:
        {
            "system": {"in_app": true, "email": true, "push": false},
            "chat": {"in_app": true, "email": false, "push": true},
            "payment": {"in_app": true, "email": true, "push": true},
        }

    Usage:
        prefs = NotificationPreference.objects.get(user=user)
        if prefs.should_send("chat", "push"):
            # Send push notification
    """

    # TODO: Implement model fields
    # user = models.OneToOneField(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.CASCADE,
    #     primary_key=True,
    #     related_name="notification_preferences",
    # )
    # preferences = models.JSONField(
    #     default=dict,
    #     blank=True,
    #     help_text="Per-type channel settings",
    # )
    # email_enabled = models.BooleanField(
    #     default=True,
    #     help_text="Global email notifications toggle",
    # )
    # push_enabled = models.BooleanField(
    #     default=True,
    #     help_text="Global push notifications toggle",
    # )
    # quiet_hours_start = models.TimeField(
    #     null=True,
    #     blank=True,
    #     help_text="Start of quiet hours (no push)",
    # )
    # quiet_hours_end = models.TimeField(
    #     null=True,
    #     blank=True,
    #     help_text="End of quiet hours",
    # )

    class Meta:
        verbose_name = "Notification Preference"
        verbose_name_plural = "Notification Preferences"

    def __str__(self) -> str:
        # TODO: Implement
        # return f"Preferences for {self.user.email}"
        return "NotificationPreference"

    # TODO: Implement methods
    # def should_send(self, notification_type: str, channel: str) -> bool:
    #     """
    #     Check if notification should be sent via channel.
    #
    #     Args:
    #         notification_type: Type of notification
    #         channel: Delivery channel
    #
    #     Returns:
    #         True if notification should be sent
    #     """
    #     # Check global toggles
    #     if channel == NotificationChannel.EMAIL and not self.email_enabled:
    #         return False
    #     if channel == NotificationChannel.PUSH and not self.push_enabled:
    #         return False
    #
    #     # Check quiet hours for push
    #     if channel == NotificationChannel.PUSH and self._in_quiet_hours():
    #         return False
    #
    #     # Check per-type preferences
    #     type_prefs = self.preferences.get(notification_type, {})
    #     return type_prefs.get(channel, True)  # Default to enabled
    #
    # def _in_quiet_hours(self) -> bool:
    #     """Check if current time is in quiet hours."""
    #     if not self.quiet_hours_start or not self.quiet_hours_end:
    #         return False
    #
    #     from django.utils import timezone
    #     now = timezone.localtime().time()
    #
    #     if self.quiet_hours_start <= self.quiet_hours_end:
    #         # Normal case: e.g., 22:00 to 08:00 next day
    #         return self.quiet_hours_start <= now <= self.quiet_hours_end
    #     else:
    #         # Overnight case: e.g., 22:00 to 08:00
    #         return now >= self.quiet_hours_start or now <= self.quiet_hours_end
    #
    # @classmethod
    # def get_default_preferences(cls) -> dict:
    #     """Get default notification preferences."""
    #     return {
    #         NotificationType.SYSTEM: {
    #             NotificationChannel.IN_APP: True,
    #             NotificationChannel.EMAIL: True,
    #             NotificationChannel.PUSH: True,
    #         },
    #         NotificationType.CHAT: {
    #             NotificationChannel.IN_APP: True,
    #             NotificationChannel.EMAIL: False,
    #             NotificationChannel.PUSH: True,
    #         },
    #         NotificationType.PAYMENT: {
    #             NotificationChannel.IN_APP: True,
    #             NotificationChannel.EMAIL: True,
    #             NotificationChannel.PUSH: True,
    #         },
    #         NotificationType.SUBSCRIPTION: {
    #             NotificationChannel.IN_APP: True,
    #             NotificationChannel.EMAIL: True,
    #             NotificationChannel.PUSH: False,
    #         },
    #         NotificationType.AI: {
    #             NotificationChannel.IN_APP: True,
    #             NotificationChannel.EMAIL: False,
    #             NotificationChannel.PUSH: False,
    #         },
    #     }
