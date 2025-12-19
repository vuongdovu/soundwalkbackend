"""
URL configuration for chat API.

URL Structure:
    Conversations:
        /conversations/                          GET, POST
        /conversations/{id}/                     GET, PATCH, DELETE
        /conversations/{id}/read/                POST
        /conversations/{id}/leave/               POST
        /conversations/{id}/transfer-ownership/  POST
        /conversations/{id}/presence/            GET

    Participants:
        /conversations/{id}/participants/        GET, POST
        /conversations/{id}/participants/{pk}/   GET, PATCH, DELETE

    Messages:
        /conversations/{id}/messages/            GET, POST
        /conversations/{id}/messages/{pk}/       GET, DELETE
        /conversations/{id}/messages/{pk}/edit/  PATCH
        /conversations/{id}/messages/{pk}/history/ GET

    Reactions:
        /conversations/{id}/messages/{pk}/reactions/ GET, POST
        /conversations/{id}/messages/{pk}/reactions/{emoji}/ DELETE
        /conversations/{id}/messages/{pk}/reactions/toggle/ POST

    Search:
        /messages/search/                        GET

    Presence:
        /presence/                               POST
        /presence/bulk/                          POST
        /presence/heartbeat/                     POST
        /presence/{user_id}/                     GET

All URLs are prefixed with /api/v1/chat/ in the main URL configuration.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from chat.views import (
    BulkPresenceView,
    ConversationViewSet,
    HeartbeatView,
    MessageSearchView,
    MessageViewSet,
    ParticipantViewSet,
    PresenceView,
    UserPresenceView,
)

# Main router for conversations
router = DefaultRouter()
router.register(r"conversations", ConversationViewSet, basename="conversation")

app_name = "chat"

urlpatterns = [
    path("", include(router.urls)),
    # Message search endpoint (searches across all user's conversations)
    path("messages/search/", MessageSearchView.as_view(), name="message-search"),
    # Presence endpoints
    path("presence/", PresenceView.as_view(), name="presence"),
    path("presence/bulk/", BulkPresenceView.as_view(), name="presence-bulk"),
    path("presence/heartbeat/", HeartbeatView.as_view(), name="presence-heartbeat"),
    path("presence/<uuid:user_id>/", UserPresenceView.as_view(), name="presence-user"),
    # Conversation presence endpoint
    path(
        "conversations/<int:conversation_pk>/presence/",
        ConversationViewSet.as_view({"get": "presence"}),
        name="conversation-presence",
    ),
    # Nested routes for participants
    path(
        "conversations/<int:conversation_pk>/participants/",
        ParticipantViewSet.as_view({"get": "list", "post": "create"}),
        name="conversation-participant-list",
    ),
    path(
        "conversations/<int:conversation_pk>/participants/<int:pk>/",
        ParticipantViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
        ),
        name="conversation-participant-detail",
    ),
    # Nested routes for messages
    path(
        "conversations/<int:conversation_pk>/messages/",
        MessageViewSet.as_view({"get": "list", "post": "create"}),
        name="conversation-message-list",
    ),
    path(
        "conversations/<int:conversation_pk>/messages/<int:pk>/",
        MessageViewSet.as_view({"get": "retrieve", "delete": "destroy"}),
        name="conversation-message-detail",
    ),
    path(
        "conversations/<int:conversation_pk>/messages/<int:pk>/edit/",
        MessageViewSet.as_view({"patch": "edit"}),
        name="conversation-message-edit",
    ),
    path(
        "conversations/<int:conversation_pk>/messages/<int:pk>/history/",
        MessageViewSet.as_view({"get": "history"}),
        name="conversation-message-history",
    ),
    # Reaction routes
    path(
        "conversations/<int:conversation_pk>/messages/<int:pk>/reactions/",
        MessageViewSet.as_view({"get": "reactions", "post": "reactions"}),
        name="conversation-message-reactions",
    ),
    path(
        "conversations/<int:conversation_pk>/messages/<int:pk>/reactions/toggle/",
        MessageViewSet.as_view({"post": "toggle_reaction"}),
        name="conversation-message-reaction-toggle",
    ),
    path(
        "conversations/<int:conversation_pk>/messages/<int:pk>/reactions/<str:emoji>/",
        MessageViewSet.as_view({"delete": "remove_reaction"}),
        name="conversation-message-reaction-detail",
    ),
]
