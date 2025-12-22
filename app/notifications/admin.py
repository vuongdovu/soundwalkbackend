"""
Django admin configuration for notification models.

Registers all notification models with the admin site:
- NotificationType
- Notification
- UserGlobalPreference
- UserCategoryPreference
- UserNotificationPreference
- NotificationDelivery
"""

from django.contrib import admin

from notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationType,
    UserCategoryPreference,
    UserGlobalPreference,
    UserNotificationPreference,
)


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
        "category",
        "is_active",
        "supports_push",
        "supports_email",
        "supports_websocket",
    ]
    list_filter = [
        "is_active",
        "category",
        "supports_push",
        "supports_email",
        "supports_websocket",
    ]
    search_fields = ["key", "display_name"]
    ordering = ["category", "key"]
    fieldsets = (
        (
            None,
            {
                "fields": ("key", "display_name", "category", "is_active"),
            },
        ),
        (
            "Templates",
            {
                "fields": ("title_template", "body_template"),
            },
        ),
        (
            "Channel Support",
            {
                "fields": ("supports_push", "supports_email", "supports_websocket"),
            },
        ),
    )


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
    search_fields = ["title", "recipient__email", "idempotency_key"]
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
        "idempotency_key",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["recipient", "actor"]


@admin.register(UserGlobalPreference)
class UserGlobalPreferenceAdmin(admin.ModelAdmin):
    """
    Admin configuration for UserGlobalPreference.

    Shows users who have muted all notifications.
    """

    list_display = ["user", "all_disabled", "updated_at"]
    list_filter = ["all_disabled"]
    search_fields = ["user__email"]
    raw_id_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(UserCategoryPreference)
class UserCategoryPreferenceAdmin(admin.ModelAdmin):
    """
    Admin configuration for UserCategoryPreference.

    Shows category-level notification preferences.
    """

    list_display = ["user", "category", "disabled", "updated_at"]
    list_filter = ["category", "disabled"]
    search_fields = ["user__email"]
    raw_id_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(UserNotificationPreference)
class UserNotificationPreferenceAdmin(admin.ModelAdmin):
    """
    Admin configuration for UserNotificationPreference.

    Shows type-level notification preferences with channel overrides.
    """

    list_display = [
        "user",
        "notification_type",
        "disabled",
        "push_enabled",
        "email_enabled",
        "websocket_enabled",
        "updated_at",
    ]
    list_filter = [
        "notification_type",
        "disabled",
        "push_enabled",
        "email_enabled",
        "websocket_enabled",
    ]
    search_fields = ["user__email", "notification_type__key"]
    raw_id_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    """
    Admin configuration for NotificationDelivery.

    Shows delivery status for each channel.
    """

    list_display = [
        "id",
        "notification",
        "channel",
        "status",
        "attempt_count",
        "sent_at",
        "delivered_at",
        "failed_at",
    ]
    list_filter = ["channel", "status", "is_permanent_failure"]
    search_fields = [
        "notification__recipient__email",
        "provider_message_id",
    ]
    ordering = ["-created_at"]
    readonly_fields = [
        "notification",
        "channel",
        "status",
        "sent_at",
        "delivered_at",
        "failed_at",
        "provider_message_id",
        "failure_reason",
        "failure_code",
        "is_permanent_failure",
        "attempt_count",
        "skipped_reason",
        "websocket_devices_targeted",
        "websocket_devices_reached",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["notification"]

    def has_add_permission(self, request):
        """Deliveries are created by the system, not manually."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Deliveries should not be deleted for audit purposes."""
        return False
