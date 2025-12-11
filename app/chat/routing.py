"""
WebSocket URL routing for chat app.

Defines WebSocket URL patterns for Django Channels.

Related files:
    - consumers.py: WebSocket consumer handlers
    - middleware.py: JWT authentication
    - config/asgi.py: ASGI application setup

URL Patterns:
    ws/chat/<conversation_id>/: Chat WebSocket endpoint

Usage in config/asgi.py:
    from chat.routing import websocket_urlpatterns

    application = ProtocolTypeRouter({
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            JWTAuthMiddleware(
                URLRouter(websocket_urlpatterns)
            )
        ),
    })
"""


# TODO: Uncomment when consumers are implemented
# from . import consumers

# WebSocket URL patterns
websocket_urlpatterns = [
    # TODO: Uncomment when consumer is implemented
    # path(
    #     "ws/chat/<int:conversation_id>/",
    #     consumers.ChatConsumer.as_asgi(),
    # ),
]
