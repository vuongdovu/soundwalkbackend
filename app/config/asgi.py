"""
ASGI config for the Django application.

ASGI (Asynchronous Server Gateway Interface) is the successor to WSGI,
designed to handle async Python web applications. This file exposes the
ASGI callable as a module-level variable named `application`.

This configuration supports:
- HTTP requests via Django
- WebSocket connections via Django Channels

Uvicorn uses this entry point to serve the Django application with full
async support, enabling:
- Async views and middleware
- WebSocket connections (with Django Channels)
- HTTP/2 support
- Better concurrency for I/O-bound operations

For more information on this file, see:
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

# Set the default Django settings module for the ASGI application
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialize Django ASGI application early to ensure settings are loaded
# before importing any models or other Django components
django_asgi_app = get_asgi_application()

# Import Channels components after Django is initialized
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from chat.middleware import JWTAuthMiddleware  # noqa: E402
from chat.routing import websocket_urlpatterns  # noqa: E402

# ASGI application that routes HTTP and WebSocket protocols
application = ProtocolTypeRouter(
    {
        # HTTP requests are handled by Django's ASGI application
        "http": django_asgi_app,
        # WebSocket connections are routed through:
        # 1. AllowedHostsOriginValidator - ensures origin matches ALLOWED_HOSTS
        # 2. JWTAuthMiddleware - authenticates user via JWT token
        # 3. URLRouter - routes to appropriate consumer based on path
        "websocket": AllowedHostsOriginValidator(
            JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        ),
    }
)
