"""
Django admin configuration for notifications app.

Registers:
    - Notification: User notifications
    - DeviceToken: Push notification tokens
    - NotificationPreference: User notification settings

Usage:
    Access via /admin/notifications/
"""

from django.contrib import admin

# TODO: Uncomment when models are implemented
# from .models import DeviceToken, Notification, NotificationPreference


# TODO: Implement admin classes
# @admin.register(Notification)
# class NotificationAdmin(admin.ModelAdmin):
#     """Admin for Notification model."""
#
#     list_display = [
#         "id",
#         "user",
#         "notification_type",
#         "title_truncated",
#         "channel",
#         "is_read",
#         "created_at",
#     ]
#     list_filter = ["notification_type", "channel", "is_read"]
#     search_fields = ["user__email", "title", "body"]
#     readonly_fields = ["created_at", "updated_at", "read_at"]
#     raw_id_fields = ["user"]
#     date_hierarchy = "created_at"
#
#     fieldsets = (
#         (None, {
#             "fields": ("user", "notification_type", "channel"),
#         }),
#         ("Content", {
#             "fields": ("title", "body", "action_url"),
#         }),
#         ("Status", {
#             "fields": ("is_read", "read_at", "expires_at"),
#         }),
#         ("Metadata", {
#             "fields": ("metadata",),
#             "classes": ("collapse",),
#         }),
#         ("Timestamps", {
#             "fields": ("created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )
#
#     def title_truncated(self, obj):
#         """Truncate title for list display."""
#         return obj.title[:50] + "..." if len(obj.title) > 50 else obj.title
#     title_truncated.short_description = "Title"


# @admin.register(DeviceToken)
# class DeviceTokenAdmin(admin.ModelAdmin):
#     """Admin for DeviceToken model."""
#
#     list_display = [
#         "user",
#         "platform",
#         "device_name",
#         "is_active",
#         "last_used_at",
#         "app_version",
#     ]
#     list_filter = ["platform", "is_active"]
#     search_fields = ["user__email", "device_id", "device_name"]
#     readonly_fields = ["token", "created_at", "updated_at", "last_used_at"]
#     raw_id_fields = ["user"]
#
#     fieldsets = (
#         (None, {
#             "fields": ("user", "platform", "is_active"),
#         }),
#         ("Device Info", {
#             "fields": ("device_id", "device_name", "app_version"),
#         }),
#         ("Token", {
#             "fields": ("token",),
#             "classes": ("collapse",),
#         }),
#         ("Timestamps", {
#             "fields": ("last_used_at", "created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )
#
#     def has_add_permission(self, request):
#         """Disable manual creation."""
#         return False


# @admin.register(NotificationPreference)
# class NotificationPreferenceAdmin(admin.ModelAdmin):
#     """Admin for NotificationPreference model."""
#
#     list_display = [
#         "user",
#         "email_enabled",
#         "push_enabled",
#         "quiet_hours_display",
#     ]
#     list_filter = ["email_enabled", "push_enabled"]
#     search_fields = ["user__email"]
#     raw_id_fields = ["user"]
#
#     fieldsets = (
#         (None, {
#             "fields": ("user",),
#         }),
#         ("Global Toggles", {
#             "fields": ("email_enabled", "push_enabled"),
#         }),
#         ("Quiet Hours", {
#             "fields": ("quiet_hours_start", "quiet_hours_end"),
#         }),
#         ("Preferences", {
#             "fields": ("preferences",),
#         }),
#     )
#
#     def quiet_hours_display(self, obj):
#         """Format quiet hours for display."""
#         if obj.quiet_hours_start and obj.quiet_hours_end:
#             return f"{obj.quiet_hours_start} - {obj.quiet_hours_end}"
#         return "Not set"
#     quiet_hours_display.short_description = "Quiet Hours"
