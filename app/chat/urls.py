"""
URL configuration for chat API.

URL Structure:
    /conversations/                          GET, POST
    /conversations/{id}/                     GET, PATCH, DELETE
    /conversations/{id}/read/                POST
    /conversations/{id}/leave/               POST
    /conversations/{id}/transfer-ownership/  POST
    /conversations/{id}/participants/        GET, POST
    /conversations/{id}/participants/{pk}/   PATCH, DELETE
    /conversations/{id}/messages/            GET, POST
    /conversations/{id}/messages/{pk}/       DELETE

All URLs are prefixed with /api/v1/chat/ in the main URL configuration.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from chat.views import ConversationViewSet, MessageViewSet, ParticipantViewSet

# Main router for conversations
router = DefaultRouter()
router.register(r"conversations", ConversationViewSet, basename="conversation")

app_name = "chat"

urlpatterns = [
    path("", include(router.urls)),
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
]
