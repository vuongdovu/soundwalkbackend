"""
URL configuration for notifications API.

Routes:
    Notifications:
        /                     - List notifications (GET)
        /{id}/                - Notification detail (GET)
        /unread-count/        - Get unread count (GET)
        /{id}/read/           - Mark single as read (POST)
        /read-all/            - Mark all as read (POST)

    Preferences:
        /preferences/             - List all preferences (GET)
        /preferences/global/      - Update global mute (PATCH)
        /preferences/category/    - Update category preference (PATCH)
        /preferences/type/        - Update type preference (PATCH)
        /preferences/bulk/        - Bulk update preferences (POST)
        /preferences/reset/       - Reset preferences (POST)

    Types:
        /types/               - List notification types (GET)
        /types/{key}/         - Get notification type detail (GET)

    Webhooks:
        /webhooks/fcm/        - FCM delivery callback (POST)
        /webhooks/email/      - Email delivery callback (POST)
"""

from django.urls import path

from rest_framework.routers import DefaultRouter

from notifications.views import (
    NotificationTypeViewSet,
    NotificationViewSet,
    PreferenceViewSet,
)
from notifications.webhooks import EmailWebhookView, FCMWebhookView

router = DefaultRouter()
router.register(r"", NotificationViewSet, basename="notification")
router.register(r"preferences", PreferenceViewSet, basename="preference")
router.register(r"types", NotificationTypeViewSet, basename="notification-type")

app_name = "notifications"
urlpatterns = router.urls + [
    path("webhooks/fcm/", FCMWebhookView.as_view(), name="fcm-webhook"),
    path("webhooks/email/", EmailWebhookView.as_view(), name="email-webhook"),
]
