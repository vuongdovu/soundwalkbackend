"""
Serializers for notification API.

This module provides DRF serializers for the notification endpoints.

Serializers:
    NotificationSerializer: Read-only serializer for notification details
    UnreadCountSerializer: Response for unread count endpoint
    MarkAllReadResponseSerializer: Response for mark all read endpoint

Usage:
    from notifications.serializers import NotificationSerializer

    serializer = NotificationSerializer(notification)
    data = serializer.data
"""

from __future__ import annotations

from rest_framework import serializers

from notifications.models import Notification


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
