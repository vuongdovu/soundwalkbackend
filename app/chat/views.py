"""
DRF views for chat app.

This module provides REST API views for:
- Conversation listing and creation
- Message history and sending
- Read state management

Note: Real-time functionality uses WebSocket (see consumers.py).
These REST endpoints are for initial data loading and fallback.

Related files:
    - services.py: ChatService
    - serializers.py: Request/response serializers
    - urls.py: URL routing
    - consumers.py: WebSocket handlers

Endpoints:
    GET /api/v1/chat/conversations/ - List conversations
    POST /api/v1/chat/conversations/ - Create conversation
    GET /api/v1/chat/conversations/<id>/ - Get conversation detail
    GET /api/v1/chat/conversations/<id>/messages/ - Get message history
    POST /api/v1/chat/conversations/<id>/messages/ - Send message
    POST /api/v1/chat/conversations/<id>/read/ - Mark as read
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    ConversationSerializer,
    CreateConversationSerializer,
    MarkAsReadSerializer,
    MessageSerializer,
    SendMessageSerializer,
)

logger = logging.getLogger(__name__)


class ConversationListView(APIView):
    """
    List and create conversations.

    GET /api/v1/chat/conversations/
        List user's conversations

    POST /api/v1/chat/conversations/
        Create new conversation
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """List user's conversations."""
        # TODO: Implement
        # from .models import Conversation
        #
        # conversations = Conversation.objects.filter(
        #     participants=request.user,
        #     participations__is_active=True,
        #     is_archived=False,
        # ).select_related().prefetch_related(
        #     "participants",
        #     "participations",
        # ).order_by("-last_message_at")
        #
        # serializer = ConversationSerializer(
        #     conversations,
        #     many=True,
        #     context={"request": request}
        # )
        # return Response(serializer.data)
        return Response(
            {"detail": "Not implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def post(self, request):
        """Create new conversation."""
        # TODO: Implement
        # from django.contrib.auth import get_user_model
        # from .services import ChatService
        #
        # serializer = CreateConversationSerializer(data=request.data)
        # serializer.is_valid(raise_exception=True)
        #
        # User = get_user_model()
        # participant_ids = serializer.validated_data.pop("participant_ids")
        # participants = User.objects.filter(id__in=participant_ids)
        #
        # # For direct conversations, use get_or_create
        # if serializer.validated_data.get("conversation_type") == "direct":
        #     if len(participants) != 1:
        #         return Response(
        #             {"detail": "Direct conversations require exactly one other participant"},
        #             status=status.HTTP_400_BAD_REQUEST
        #         )
        #     conversation = ChatService.get_or_create_direct_conversation(
        #         user1=request.user,
        #         user2=participants.first(),
        #     )
        # else:
        #     conversation = ChatService.create_conversation(
        #         creator=request.user,
        #         participants=list(participants) + [request.user],
        #         **serializer.validated_data
        #     )
        #
        # return Response(
        #     ConversationSerializer(conversation, context={"request": request}).data,
        #     status=status.HTTP_201_CREATED
        # )
        return Response(
            {"detail": "Not implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class ConversationDetailView(APIView):
    """
    Get conversation detail.

    GET /api/v1/chat/conversations/<id>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        """Get conversation detail."""
        # TODO: Implement
        # from .models import Conversation
        #
        # try:
        #     conversation = Conversation.objects.select_related().prefetch_related(
        #         "participants",
        #         "participations",
        #     ).get(
        #         id=conversation_id,
        #         participants=request.user,
        #         participations__is_active=True,
        #     )
        # except Conversation.DoesNotExist:
        #     return Response(
        #         {"detail": "Conversation not found"},
        #         status=status.HTTP_404_NOT_FOUND
        #     )
        #
        # serializer = ConversationSerializer(conversation, context={"request": request})
        # return Response(serializer.data)
        return Response(
            {"detail": "Not implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class MessageListView(APIView):
    """
    Get message history and send messages.

    GET /api/v1/chat/conversations/<id>/messages/
        Get message history

    POST /api/v1/chat/conversations/<id>/messages/
        Send new message
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        """Get message history."""
        # TODO: Implement
        # from .models import Conversation
        # from .services import ChatService
        #
        # try:
        #     conversation = Conversation.objects.get(
        #         id=conversation_id,
        #         participants=request.user,
        #     )
        # except Conversation.DoesNotExist:
        #     return Response(
        #         {"detail": "Conversation not found"},
        #         status=status.HTTP_404_NOT_FOUND
        #     )
        #
        # before_id = request.query_params.get("before")
        # limit = int(request.query_params.get("limit", 50))
        #
        # messages = ChatService.get_conversation_history(
        #     conversation=conversation,
        #     user=request.user,
        #     before_id=int(before_id) if before_id else None,
        #     limit=min(limit, 100),
        # )
        #
        # serializer = MessageSerializer(messages, many=True)
        # return Response(serializer.data)
        return Response(
            {"detail": "Not implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def post(self, request, conversation_id):
        """Send new message."""
        # TODO: Implement
        # from .models import Conversation
        # from .services import ChatService
        #
        # try:
        #     conversation = Conversation.objects.get(
        #         id=conversation_id,
        #         participants=request.user,
        #         participations__is_active=True,
        #     )
        # except Conversation.DoesNotExist:
        #     return Response(
        #         {"detail": "Conversation not found"},
        #         status=status.HTTP_404_NOT_FOUND
        #     )
        #
        # serializer = SendMessageSerializer(data=request.data)
        # serializer.is_valid(raise_exception=True)
        #
        # message = ChatService.send_message(
        #     conversation=conversation,
        #     sender=request.user,
        #     **serializer.validated_data
        # )
        #
        # return Response(
        #     MessageSerializer(message).data,
        #     status=status.HTTP_201_CREATED
        # )
        return Response(
            {"detail": "Not implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class MarkAsReadView(APIView):
    """
    Mark conversation as read.

    POST /api/v1/chat/conversations/<id>/read/
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id):
        """Mark messages as read."""
        # TODO: Implement
        # from .models import Conversation
        # from .services import ChatService
        #
        # try:
        #     conversation = Conversation.objects.get(
        #         id=conversation_id,
        #         participants=request.user,
        #     )
        # except Conversation.DoesNotExist:
        #     return Response(
        #         {"detail": "Conversation not found"},
        #         status=status.HTTP_404_NOT_FOUND
        #     )
        #
        # serializer = MarkAsReadSerializer(data=request.data)
        # serializer.is_valid(raise_exception=True)
        #
        # ChatService.mark_as_read(
        #     conversation=conversation,
        #     user=request.user,
        #     message_id=serializer.validated_data["message_id"],
        # )
        #
        # return Response({"status": "marked"})
        return Response(
            {"detail": "Not implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class UnreadCountsView(APIView):
    """
    Get unread message counts.

    GET /api/v1/chat/unread-counts/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get unread counts per conversation."""
        # TODO: Implement
        # from .services import ChatService
        #
        # counts = ChatService.get_unread_counts(request.user)
        # return Response(counts)
        return Response(
            {"detail": "Not implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
