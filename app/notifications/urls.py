"""
URL configuration for notifications API.

Routes:
    /                     - List notifications (GET)
    /{id}/                - Notification detail (GET)
    /unread-count/        - Get unread count (GET)
    /{id}/read/           - Mark single as read (POST)
    /read-all/            - Mark all as read (POST)
"""

from rest_framework.routers import DefaultRouter

from notifications.views import NotificationViewSet

router = DefaultRouter()
router.register(r"", NotificationViewSet, basename="notification")

app_name = "notifications"
urlpatterns = router.urls
