"""
WebSocket authentication middleware.

Provides JWT authentication for WebSocket connections.
Supports token via query string or subprotocol.

Related files:
    - routing.py: WebSocket URL patterns
    - consumers.py: WebSocket handlers
    - config/asgi.py: ASGI configuration

Token Passing Methods:
    1. Query string: ws://host/ws/chat/1/?token=<jwt_token>
    2. Subprotocol: Sec-WebSocket-Protocol: jwt, <jwt_token>

Usage in config/asgi.py:
    from chat.middleware import JWTAuthMiddleware

    application = ProtocolTypeRouter({
        "websocket": JWTAuthMiddleware(
            URLRouter(websocket_urlpatterns)
        ),
    })
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

# TODO: Uncomment when implementing
# from channels.middleware import BaseMiddleware
# from channels.db import database_sync_to_async
# from django.contrib.auth.models import AnonymousUser
# from rest_framework_simplejwt.tokens import AccessToken
# from rest_framework_simplejwt.exceptions import TokenError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# TODO: Implement JWT authentication middleware
# class JWTAuthMiddleware(BaseMiddleware):
#     """
#     JWT authentication middleware for WebSocket connections.
#
#     Extracts JWT token from query string or subprotocol,
#     validates it, and attaches the user to the scope.
#
#     Token sources (in order of precedence):
#         1. Query string: ?token=<jwt_token>
#         2. Subprotocol: Sec-WebSocket-Protocol: jwt, <jwt_token>
#
#     Usage:
#         # In asgi.py
#         application = ProtocolTypeRouter({
#             "websocket": JWTAuthMiddleware(
#                 URLRouter(websocket_urlpatterns)
#             ),
#         })
#
#         # Client connection with query string
#         ws = new WebSocket("ws://host/ws/chat/1/?token=eyJ...")
#
#         # Client connection with subprotocol
#         ws = new WebSocket("ws://host/ws/chat/1/", ["jwt", "eyJ..."])
#     """
#
#     async def __call__(self, scope, receive, send):
#         """
#         Process WebSocket connection.
#
#         Authenticates user and adds to scope before
#         passing to inner application.
#         """
#         # Get token from query string or subprotocol
#         token = self._get_token_from_query(scope) or self._get_token_from_subprotocol(scope)
#
#         if token:
#             scope["user"] = await self._get_user_from_token(token)
#         else:
#             scope["user"] = AnonymousUser()
#
#         return await super().__call__(scope, receive, send)
#
#     def _get_token_from_query(self, scope) -> str | None:
#         """Extract token from query string."""
#         from urllib.parse import parse_qs
#
#         query_string = scope.get("query_string", b"").decode()
#         params = parse_qs(query_string)
#         token_list = params.get("token", [])
#
#         return token_list[0] if token_list else None
#
#     def _get_token_from_subprotocol(self, scope) -> str | None:
#         """
#         Extract token from WebSocket subprotocol.
#
#         Expects: Sec-WebSocket-Protocol: jwt, <token>
#         """
#         subprotocols = scope.get("subprotocols", [])
#
#         if len(subprotocols) >= 2 and subprotocols[0] == "jwt":
#             return subprotocols[1]
#
#         return None
#
#     @database_sync_to_async
#     def _get_user_from_token(self, token: str):
#         """
#         Validate JWT token and get user.
#
#         Args:
#             token: JWT access token
#
#         Returns:
#             User instance if valid, AnonymousUser otherwise
#         """
#         from django.contrib.auth import get_user_model
#
#         User = get_user_model()
#
#         try:
#             # Validate token
#             access_token = AccessToken(token)
#             user_id = access_token["user_id"]
#
#             # Get user
#             user = User.objects.get(id=user_id)
#
#             if not user.is_active:
#                 logger.warning(f"Inactive user attempted WebSocket connection: {user_id}")
#                 return AnonymousUser()
#
#             return user
#
#         except TokenError as e:
#             logger.warning(f"Invalid JWT token: {e}")
#             return AnonymousUser()
#         except User.DoesNotExist:
#             logger.warning(f"User not found for token")
#             return AnonymousUser()
#         except Exception as e:
#             logger.error(f"Error authenticating WebSocket: {e}")
#             return AnonymousUser()


class JWTAuthMiddleware:
    """
    Placeholder for JWT authentication middleware.

    See commented implementation above for full details.
    Requires Django Channels to be installed and configured.
    """

    def __init__(self, inner):
        """Initialize middleware with inner application."""
        self.inner = inner

    async def __call__(self, scope, receive, send):
        """Pass through to inner application."""
        return await self.inner(scope, receive, send)
