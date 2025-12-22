"""
Serializers for notification API.

This module provides DRF serializers for the notification endpoints.

Serializers:
    NotificationSerializer: Read-only serializer for notification details
    UnreadCountSerializer: Response for unread count endpoint
    MarkAllReadResponseSerializer: Response for mark all read endpoint
    GlobalPreferenceSerializer: Update global mute setting
    CategoryPreferenceSerializer: Update category preference
    TypePreferenceSerializer: Update type-level preferences
    BulkPreferenceSerializer: Bulk update type preferences
    UserPreferencesResponseSerializer: Complete preferences response
    NotificationDeliverySerializer: Delivery status details

Usage:
    from notifications.serializers import NotificationSerializer

    serializer = NotificationSerializer(notification)
    data = serializer.data
"""

from __future__ import annotations

from rest_framework import serializers

from notifications.models import (
    Notification,
    NotificationCategory,
    NotificationDelivery,
    NotificationType,
)


class NotificationSerializer(serializers.ModelSerializer):
    """
    Serializer for Notification model.

    Read-only serializer that includes:
    - Basic notification fields (id, title, body, data, is_read, created_at)
    - type_key from related NotificationType
    - actor_name derived from actor user (handles SET_NULL)

    Usage:
        serializer = NotificationSerializer(notification)
        serializer = NotificationSerializer(notifications, many=True)
    """

    type_key = serializers.CharField(source="notification_type.key", read_only=True)
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "type_key",
            "title",
            "body",
            "data",
            "actor_name",
            "is_read",
            "created_at",
        ]
        read_only_fields = fields

    def get_actor_name(self, obj: Notification) -> str | None:
        """
        Get actor's display name.

        Returns None for system notifications (no actor) or
        when actor was deleted (SET_NULL).

        Args:
            obj: Notification instance

        Returns:
            Actor's email or None if no actor
        """
        if obj.actor is None:
            return None
        # User model uses email-only auth, so use email as identifier
        # Could be enhanced to use Profile.display_name if available
        return obj.actor.email


class UnreadCountSerializer(serializers.Serializer):
    """
    Response serializer for unread count endpoint.

    Fields:
        unread_count: Integer count of unread notifications
    """

    unread_count = serializers.IntegerField()


class MarkAllReadResponseSerializer(serializers.Serializer):
    """
    Response serializer for mark all read endpoint.

    Fields:
        marked_count: Integer count of notifications marked as read
    """

    marked_count = serializers.IntegerField()


# ============================================================================
# Preference Serializers
# ============================================================================


class GlobalPreferenceSerializer(serializers.Serializer):
    """
    Serializer for updating global notification preferences.

    Fields:
        all_disabled: If True, mute all notifications for the user
    """

    all_disabled = serializers.BooleanField(
        help_text="Set to true to disable all notifications"
    )


class CategoryPreferenceSerializer(serializers.Serializer):
    """
    Serializer for updating category notification preferences.

    Fields:
        category: The notification category to update
        disabled: If True, disable all notifications in this category
    """

    category = serializers.ChoiceField(
        choices=NotificationCategory.choices,
        help_text="Notification category to update",
    )
    disabled = serializers.BooleanField(
        help_text="Set to true to disable notifications in this category"
    )


class TypePreferenceSerializer(serializers.Serializer):
    """
    Serializer for updating type-level notification preferences.

    Fields:
        type_key: The notification type key to update
        disabled: If True, disable this notification type entirely
        push_enabled: Override push channel (null = inherit from type)
        email_enabled: Override email channel (null = inherit from type)
        websocket_enabled: Override websocket channel (null = inherit from type)
    """

    type_key = serializers.CharField(
        max_length=100,
        help_text="Notification type key to update",
    )
    disabled = serializers.BooleanField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Set to true to disable this notification type entirely",
    )
    push_enabled = serializers.BooleanField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Override push channel (null = inherit from type default)",
    )
    email_enabled = serializers.BooleanField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Override email channel (null = inherit from type default)",
    )
    websocket_enabled = serializers.BooleanField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Override websocket channel (null = inherit from type default)",
    )

    def validate_type_key(self, value: str) -> str:
        """Validate that the notification type exists."""
        if not NotificationType.objects.filter(key=value).exists():
            raise serializers.ValidationError(
                f"Notification type '{value}' does not exist"
            )
        return value


class BulkPreferenceSerializer(serializers.Serializer):
    """
    Serializer for bulk updating multiple type preferences.

    Fields:
        preferences: List of type preference updates
    """

    preferences = TypePreferenceSerializer(
        many=True,
        help_text="List of type preference updates",
    )


class TypePreferenceResponseSerializer(serializers.Serializer):
    """
    Response serializer for a single type preference.

    Read-only representation of a user's type preference.
    """

    type_key = serializers.CharField()
    type_name = serializers.CharField()
    disabled = serializers.BooleanField()
    push_enabled = serializers.BooleanField(allow_null=True)
    email_enabled = serializers.BooleanField(allow_null=True)
    websocket_enabled = serializers.BooleanField(allow_null=True)


class CategoryPreferenceResponseSerializer(serializers.Serializer):
    """
    Response serializer for a single category preference.

    Read-only representation of a user's category preference.
    """

    category = serializers.CharField()
    disabled = serializers.BooleanField()


class GlobalPreferenceResponseSerializer(serializers.Serializer):
    """
    Response serializer for global preferences.

    Read-only representation of user's global settings.
    """

    all_disabled = serializers.BooleanField()


class UserPreferencesResponseSerializer(serializers.Serializer):
    """
    Complete response serializer for all user preferences.

    Returns the full preference state including:
    - Global mute status
    - All category preferences
    - All type preferences with channel overrides
    """

    global_preferences = GlobalPreferenceResponseSerializer(source="global")
    categories = CategoryPreferenceResponseSerializer(many=True)
    types = TypePreferenceResponseSerializer(many=True)


class ResetPreferencesResponseSerializer(serializers.Serializer):
    """
    Response serializer for reset preferences endpoint.

    Fields:
        deleted_count: Number of preference records deleted
    """

    deleted_count = serializers.IntegerField()


# ============================================================================
# Delivery Serializers
# ============================================================================


class NotificationDeliverySerializer(serializers.ModelSerializer):
    """
    Serializer for NotificationDelivery model.

    Read-only serializer showing delivery status for a channel.

    Fields:
        id: Delivery UUID
        channel: Delivery channel (push, email, websocket)
        status: Current status (pending, sent, delivered, failed, skipped)
        sent_at: When the notification was sent to the provider
        delivered_at: When delivery was confirmed
        failed_at: When delivery failed (if applicable)
        failure_reason: Human-readable failure reason
        failure_code: Machine-readable failure code
        skipped_reason: Reason for skipping delivery (if applicable)
        attempt_count: Number of delivery attempts
    """

    class Meta:
        model = NotificationDelivery
        fields = [
            "id",
            "channel",
            "status",
            "sent_at",
            "delivered_at",
            "failed_at",
            "failure_reason",
            "failure_code",
            "skipped_reason",
            "attempt_count",
        ]
        read_only_fields = fields


class NotificationWithDeliverySerializer(NotificationSerializer):
    """
    Extended notification serializer with delivery status.

    Includes all fields from NotificationSerializer plus
    delivery status for each channel.
    """

    deliveries = NotificationDeliverySerializer(many=True, read_only=True)

    class Meta(NotificationSerializer.Meta):
        fields = NotificationSerializer.Meta.fields + ["deliveries"]
        read_only_fields = fields


class NotificationTypeSerializer(serializers.ModelSerializer):
    """
    Serializer for NotificationType model.

    Used to list available notification types with their settings.
    """

    class Meta:
        model = NotificationType
        fields = [
            "key",
            "display_name",
            "category",
            "supports_push",
            "supports_email",
            "supports_websocket",
            "is_active",
        ]
        read_only_fields = fields
