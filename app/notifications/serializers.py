"""
DRF serializers for notifications app.

This module provides serializers for:
- Notification display
- Device token registration
- Preference management

Related files:
    - models.py: Notification, DeviceToken, NotificationPreference
    - views.py: Notification API views

Usage:
    serializer = NotificationSerializer(notification)
    data = serializer.data
"""

from __future__ import annotations

from rest_framework import serializers


class NotificationSerializer(serializers.Serializer):
    """
    Notification serializer for API responses.

    Fields:
        id: Notification ID
        notification_type: Type of notification
        title: Notification title
        body: Notification body
        is_read: Read status
        read_at: When read
        action_url: Click action URL
        metadata: Additional data
        created_at: Creation time

    Usage:
        notifications = user.notifications.all()[:20]
        serializer = NotificationSerializer(notifications, many=True)
    """

    # TODO: Implement serializer fields
    # id = serializers.IntegerField(read_only=True)
    # notification_type = serializers.CharField(read_only=True)
    # title = serializers.CharField(read_only=True)
    # body = serializers.CharField(read_only=True)
    # is_read = serializers.BooleanField(read_only=True)
    # read_at = serializers.DateTimeField(read_only=True, allow_null=True)
    # action_url = serializers.URLField(read_only=True, allow_blank=True)
    # metadata = serializers.JSONField(read_only=True)
    # created_at = serializers.DateTimeField(read_only=True)
    pass


class RegisterDeviceSerializer(serializers.Serializer):
    """
    Serializer for device token registration.

    Fields:
        token: FCM/APNs token
        platform: Device platform (ios/android/web)
        device_id: Unique device identifier
        device_name: Optional human-readable name
        app_version: Optional app version

    Usage:
        serializer = RegisterDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = NotificationService.register_device(
            user=request.user,
            **serializer.validated_data
        )
    """

    token = serializers.CharField(
        help_text="FCM or APNs device token",
    )
    platform = serializers.ChoiceField(
        choices=["ios", "android", "web"],
        help_text="Device platform",
    )
    device_id = serializers.CharField(
        max_length=255,
        help_text="Unique device identifier",
    )
    device_name = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text="Human-readable device name",
    )
    app_version = serializers.CharField(
        max_length=20,
        required=False,
        allow_blank=True,
        help_text="App version",
    )


class UnregisterDeviceSerializer(serializers.Serializer):
    """
    Serializer for device token unregistration.

    Fields:
        device_id: Device ID to unregister

    Usage:
        serializer = UnregisterDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        NotificationService.unregister_device(
            serializer.validated_data["device_id"]
        )
    """

    device_id = serializers.CharField(
        max_length=255,
        help_text="Device ID to unregister",
    )


class MarkAsReadSerializer(serializers.Serializer):
    """
    Serializer for marking notifications as read.

    Fields:
        notification_ids: List of notification IDs to mark

    Usage:
        serializer = MarkAsReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        NotificationService.mark_as_read(
            notification_ids=serializer.validated_data["notification_ids"],
            user=request.user
        )
    """

    notification_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        help_text="List of notification IDs to mark as read",
    )


class NotificationPreferenceSerializer(serializers.Serializer):
    """
    Serializer for notification preferences.

    Fields:
        preferences: Per-type channel settings (JSON)
        email_enabled: Global email toggle
        push_enabled: Global push toggle
        quiet_hours_start: Quiet hours start time
        quiet_hours_end: Quiet hours end time

    Preferences structure:
        {
            "system": {"in_app": true, "email": true, "push": false},
            "chat": {"in_app": true, "email": false, "push": true},
        }

    Usage:
        prefs = NotificationService.get_preferences(user)
        serializer = NotificationPreferenceSerializer(prefs)
    """

    # TODO: Implement serializer fields
    # preferences = serializers.JSONField(required=False)
    # email_enabled = serializers.BooleanField(required=False)
    # push_enabled = serializers.BooleanField(required=False)
    # quiet_hours_start = serializers.TimeField(required=False, allow_null=True)
    # quiet_hours_end = serializers.TimeField(required=False, allow_null=True)
    pass


class UnreadCountSerializer(serializers.Serializer):
    """
    Serializer for unread notification counts.

    Fields:
        total: Total unread count
        system: System notification count
        chat: Chat notification count
        payment: Payment notification count
        subscription: Subscription notification count
        ai: AI notification count

    Usage:
        counts = NotificationService.get_unread_count(user)
        serializer = UnreadCountSerializer(counts)
    """

    total = serializers.IntegerField(read_only=True)
    system = serializers.IntegerField(read_only=True, default=0)
    chat = serializers.IntegerField(read_only=True, default=0)
    payment = serializers.IntegerField(read_only=True, default=0)
    subscription = serializers.IntegerField(read_only=True, default=0)
    ai = serializers.IntegerField(read_only=True, default=0)
