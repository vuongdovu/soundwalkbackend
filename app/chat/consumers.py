"""
WebSocket consumers for the chat application.

This module implements the WebSocket consumer for real-time chat functionality,
handling connection management, message broadcasting, and integration with
the chat service layer.

Consumers:
    ChatConsumer: Handles WebSocket connections for conversations

Authentication:
    Users are authenticated via JWT token passed as query parameter.
    The JWTAuthMiddlewareStack attaches the user to self.scope["user"].

Channel Groups:
    Each conversation has a channel group named "chat_{conversation_id}".
    Connected users join the group and receive broadcast messages.

Message Types (from client):
    - message: Send a new message to the conversation
    - typing: Broadcast typing indicator
    - read: Mark messages as read

Message Types (to client):
    - message: New message in conversation
    - typing: User is typing
    - error: Error response
"""

from __future__ import annotations

import logging
from uuid import UUID

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import AnonymousUser

from chat.models import Conversation, Participant
from chat.services import MessageService

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for real-time chat functionality.

    Handles:
        - Connection authentication and authorization
        - Joining/leaving conversation channel groups
        - Sending and receiving messages
        - Typing indicators
        - Read receipts

    Attributes:
        conversation_id: UUID of the connected conversation
        conversation: Conversation instance (after connect)
        room_group_name: Channel layer group name for the conversation
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conversation_id: UUID | None = None
        self.conversation: Conversation | None = None
        self.room_group_name: str | None = None

    async def connect(self):
        """
        Handle WebSocket connection.

        Validates:
            1. User is authenticated
            2. Conversation exists
            3. User is a participant in the conversation

        On success, joins the channel group and accepts the connection.
        """
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.room_group_name = f"chat_{self.conversation_id}"

        user = self.scope.get("user")

        if not user or isinstance(user, AnonymousUser):
            logger.warning(
                f"Rejected unauthenticated connection to conversation {self.conversation_id}"
            )
            await self.close(code=4001)
            return

        self.conversation = await self._get_conversation()
        if not self.conversation:
            logger.warning(
                f"User {user.id} tried to connect to non-existent "
                f"conversation {self.conversation_id}"
            )
            await self.close(code=4004)
            return

        is_participant = await self._is_user_participant(user)
        if not is_participant:
            logger.warning(
                f"User {user.id} is not a participant in "
                f"conversation {self.conversation_id}"
            )
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name,
        )

        await self.accept()
        logger.info(f"User {user.id} connected to conversation {self.conversation_id}")

    async def disconnect(self, close_code):
        """
        Handle WebSocket disconnection.

        Leaves the channel group if one was joined.
        """
        if self.room_group_name:
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name,
            )
            user = self.scope.get("user")
            user_id = (
                user.id if user and not isinstance(user, AnonymousUser) else "anonymous"
            )
            logger.info(
                f"User {user_id} disconnected from conversation {self.conversation_id}"
            )

    async def receive_json(self, content):
        """
        Handle incoming WebSocket messages.

        Expected message format:
            {"type": "message", "content": "Hello!"}
            {"type": "message", "content": "Reply", "parent_id": 123}
            {"type": "typing", "is_typing": true}

        Args:
            content: Parsed JSON message from client
        """
        message_type = content.get("type")
        user = self.scope["user"]

        if message_type == "message":
            await self._handle_message(user, content)
        elif message_type == "typing":
            await self._handle_typing(user, content)
        else:
            await self.send_json(
                {
                    "type": "error",
                    "message": f"Unknown message type: {message_type}",
                }
            )

    async def _handle_message(self, user, content):
        """
        Handle incoming chat message.

        Creates the message via MessageService and broadcasts to the group.
        """
        message_content = content.get("content", "").strip()
        parent_id = content.get("parent_id")

        if not message_content:
            await self.send_json(
                {
                    "type": "error",
                    "message": "Message content cannot be empty",
                }
            )
            return

        result = await self._send_message(
            user=user,
            content=message_content,
            parent_id=parent_id,
        )

        if not result["success"]:
            await self.send_json(
                {
                    "type": "error",
                    "message": result["error"],
                }
            )
            return

        message_data = result["data"]
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat.message",
                "message": message_data,
            },
        )

    async def _handle_typing(self, user, content):
        """
        Handle typing indicator.

        Broadcasts typing status to all other participants.
        """
        is_typing = content.get("is_typing", False)

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat.typing",
                "user_id": user.id,
                "is_typing": is_typing,
            },
        )

    async def chat_message(self, event):
        """
        Handle chat.message events from channel layer.

        Sends the message to the WebSocket client.
        """
        await self.send_json(
            {
                "type": "message",
                "message": event["message"],
            }
        )

    async def chat_typing(self, event):
        """
        Handle chat.typing events from channel layer.

        Sends typing indicator to the WebSocket client (except sender).
        """
        user = self.scope.get("user")
        if user and user.id == event["user_id"]:
            return

        await self.send_json(
            {
                "type": "typing",
                "user_id": event["user_id"],
                "is_typing": event["is_typing"],
            }
        )

    @database_sync_to_async
    def _get_conversation(self) -> Conversation | None:
        """Get conversation by ID."""
        try:
            return Conversation.objects.get(
                id=self.conversation_id,
                is_deleted=False,
            )
        except Conversation.DoesNotExist:
            return None

    @database_sync_to_async
    def _is_user_participant(self, user) -> bool:
        """Check if user is an active participant in the conversation."""
        return Participant.objects.filter(
            conversation_id=self.conversation_id,
            user=user,
            left_at__isnull=True,
        ).exists()

    @database_sync_to_async
    def _send_message(self, user, content: str, parent_id: int | None) -> dict:
        """
        Send a message using MessageService.

        Returns dict with success status and either data or error.
        """
        result = MessageService.send_message(
            conversation=self.conversation,
            sender=user,
            content=content,
            parent_message_id=parent_id,
        )

        if result.success:
            message = result.data
            return {
                "success": True,
                "data": {
                    "id": message.id,
                    "sender_id": message.sender_id,
                    "content": message.content,
                    "message_type": message.message_type,
                    "parent_message_id": message.parent_message_id,
                    "created_at": message.created_at.isoformat(),
                },
            }
        else:
            return {
                "success": False,
                "error": result.error,
            }
