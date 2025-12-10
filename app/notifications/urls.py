"""
URL configuration for notifications app.

Routes:
    GET / - List notifications
    POST /mark-read/ - Mark notifications as read
    POST /mark-all-read/ - Mark all as read
    GET /unread-count/ - Get unread counts
    POST /devices/ - Register device
    DELETE /devices/ - Unregister device
    GET /preferences/ - Get preferences
    PUT /preferences/ - Update preferences

Usage in config/urls.py:
    path("api/v1/notifications/", include("notifications.urls")),
"""

from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    # Notifications
    path(
        "",
        views.NotificationListView.as_view(),
        name="list",
    ),
    path(
        "mark-read/",
        views.MarkAsReadView.as_view(),
        name="mark-read",
    ),
    path(
        "mark-all-read/",
        views.MarkAllAsReadView.as_view(),
        name="mark-all-read",
    ),
    path(
        "unread-count/",
        views.UnreadCountView.as_view(),
        name="unread-count",
    ),
    # Device tokens
    path(
        "devices/",
        views.DeviceTokenView.as_view(),
        name="devices",
    ),
    # Preferences
    path(
        "preferences/",
        views.NotificationPreferenceView.as_view(),
        name="preferences",
    ),
]
