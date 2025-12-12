"""
Django admin configuration for notification models.

Registers NotificationType and Notification with the admin site.
"""

from django.contrib import admin

from notifications.models import Notification, NotificationType


@admin.register(NotificationType)
class NotificationTypeAdmin(admin.ModelAdmin):
    """
    Admin configuration for NotificationType.

    Provides management of notification type definitions including
    templates and channel support flags.
    """

    list_display = [
        "key",
        "display_name",
        "is_active",
        "supports_push",
        "supports_email",
        "supports_websocket",
    ]
    list_filter = ["is_active", "supports_push", "supports_email", "supports_websocket"]
    search_fields = ["key", "display_name"]
    ordering = ["key"]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """
    Admin configuration for Notification.

    Provides read-only view of notifications for debugging and support.
    """

    list_display = [
        "id",
        "notification_type",
        "recipient",
        "title",
        "is_read",
        "created_at",
    ]
    list_filter = ["is_read", "notification_type", "created_at"]
    search_fields = ["title", "recipient__email"]
    ordering = ["-created_at"]
    readonly_fields = [
        "notification_type",
        "recipient",
        "actor",
        "title",
        "body",
        "data",
        "content_type",
        "object_id",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["recipient", "actor"]
