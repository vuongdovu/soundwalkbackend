"""
URL configuration for chat app.

Routes:
    GET/POST /conversations/ - List/create conversations
    GET /conversations/<id>/ - Get conversation detail
    GET/POST /conversations/<id>/messages/ - Message history/send
    POST /conversations/<id>/read/ - Mark as read
    GET /unread-counts/ - Get unread counts

Usage in config/urls.py:
    path("api/v1/chat/", include("chat.urls")),
"""

from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    # Conversations
    path(
        "conversations/",
        views.ConversationListView.as_view(),
        name="conversation-list",
    ),
    path(
        "conversations/<int:conversation_id>/",
        views.ConversationDetailView.as_view(),
        name="conversation-detail",
    ),
    # Messages
    path(
        "conversations/<int:conversation_id>/messages/",
        views.MessageListView.as_view(),
        name="message-list",
    ),
    path(
        "conversations/<int:conversation_id>/read/",
        views.MarkAsReadView.as_view(),
        name="mark-read",
    ),
    # Counts
    path(
        "unread-counts/",
        views.UnreadCountsView.as_view(),
        name="unread-counts",
    ),
]
