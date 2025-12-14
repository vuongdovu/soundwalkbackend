"""
WebSocket URL routing for the chat application.

This module defines the URL patterns for WebSocket connections,
mapping paths to their corresponding consumers.

URL Patterns:
    ws/chat/<conversation_id>/ - Connect to a specific conversation

Authentication:
    JWT token should be passed as query parameter: ?token=<jwt_access_token>
    The JWTAuthMiddlewareStack will validate the token and attach the user
    to the consumer's scope.
"""

from django.urls import path

from chat import consumers

websocket_urlpatterns = [
    path(
        "ws/chat/<uuid:conversation_id>/",
        consumers.ChatConsumer.as_asgi(),
    ),
]
