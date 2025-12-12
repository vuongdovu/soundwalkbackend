"""
ViewSets for chat API.

This module provides REST API endpoints for the chat system:
- ConversationViewSet: Conversation CRUD and actions
- ParticipantViewSet: Participant management (nested under conversation)
- MessageViewSet: Message operations (nested under conversation)

URL Structure:
    /api/v1/chat/conversations/                          GET, POST
    /api/v1/chat/conversations/{id}/                     GET, PATCH, DELETE
    /api/v1/chat/conversations/{id}/read/                POST
    /api/v1/chat/conversations/{id}/leave/               POST
    /api/v1/chat/conversations/{id}/transfer-ownership/  POST
    /api/v1/chat/conversations/{id}/participants/        GET, POST
    /api/v1/chat/conversations/{id}/participants/{pk}/   PATCH, DELETE
    /api/v1/chat/conversations/{id}/messages/            GET, POST
    /api/v1/chat/conversations/{id}/messages/{pk}/       DELETE

Design Decisions:
    - ViewSets use DRF's ModelViewSet for standard CRUD
    - Custom actions for leave, read, transfer-ownership
    - Nested views use parent_lookup_kwargs for conversation context
    - All operations use service layer for business logic
    - Permissions are enforced at both view and service level
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chat.models import Conversation, ConversationType, Message, Participant
from chat.pagination import ConversationCursorPagination, MessageCursorPagination
from chat.permissions import (
    CanManageParticipants,
    CanModifyMessage,
    IsConversationAdminOrOwner,
    IsConversationOwner,
    IsConversationParticipant,
    IsGroupConversation,
)
from chat.serializers import (
    ConversationCreateSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    ConversationUpdateSerializer,
    MessageCreateSerializer,
    MessageSerializer,
    ParticipantCreateSerializer,
    ParticipantSerializer,
    ParticipantUpdateSerializer,
)
from chat.services import (
    ConversationService,
    MessageService,
    ParticipantService,
)

User = get_user_model()


class ConversationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for conversation operations.

    list:
        Get all conversations for the current user.
        Returns paginated list with unread counts and last message preview.

    create:
        Create a new conversation (direct or group).
        For direct: returns existing if found, creates if not.
        For group: creates new group with specified participants.

    retrieve:
        Get conversation details including all participants.

    partial_update:
        Update group conversation title.
        Only admins and owners can update.

    destroy:
        Soft delete a group conversation.
        Only owners can delete groups.
        Direct conversations cannot be deleted.

    read:
        Mark conversation as read.
        Updates participant's last_read_at timestamp.

    leave:
        Leave the conversation.
        For groups: triggers ownership transfer if owner leaves.
        For direct: marks participation as ended.

    transfer_ownership:
        Transfer group ownership to another participant.
        Current owner becomes admin.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = ConversationCursorPagination

    def get_queryset(self):
        """Filter to conversations where user is an active participant."""
        if not self.request.user.is_authenticated:
            return Conversation.objects.none()

        return (
            Conversation.objects.filter(
                participants__user=self.request.user,
                participants__left_at__isnull=True,
                is_deleted=False,
            )
            .select_related("created_by")
            .prefetch_related("participants__user__profile")
            .distinct()
            .order_by("-last_message_at", "-created_at")
        )

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == "list":
            return ConversationListSerializer
        if self.action == "create":
            return ConversationCreateSerializer
        if self.action in ("update", "partial_update"):
            return ConversationUpdateSerializer
        return ConversationDetailSerializer

    def get_permissions(self):
        """Return permissions based on action."""
        if self.action in ("update", "partial_update"):
            return [
                IsAuthenticated(),
                IsConversationParticipant(),
                IsConversationAdminOrOwner(),
                IsGroupConversation(),
            ]
        if self.action == "destroy":
            return [
                IsAuthenticated(),
                IsConversationParticipant(),
                IsConversationOwner(),
                IsGroupConversation(),
            ]
        if self.action == "transfer_ownership":
            return [
                IsAuthenticated(),
                IsConversationParticipant(),
                IsConversationOwner(),
                IsGroupConversation(),
            ]
        if self.action in ("retrieve", "read", "leave"):
            return [
                IsAuthenticated(),
                IsConversationParticipant(),
            ]
        return [IsAuthenticated()]

    def create(self, request):
        """Create a conversation (direct or group)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        conversation_type = data["conversation_type"]
        participant_ids = data["participant_ids"]

        if conversation_type == ConversationType.DIRECT:
            # Direct conversation: get or create
            other_user = get_object_or_404(User, id=participant_ids[0])
            result = ConversationService.create_direct(
                user1=request.user,
                user2=other_user,
            )
        else:
            # Group conversation: create new
            members = list(User.objects.filter(id__in=participant_ids))
            result = ConversationService.create_group(
                creator=request.user,
                title=data.get("title", ""),
                initial_members=members,
            )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Return detailed view of created conversation
        output_serializer = ConversationDetailSerializer(
            result.data, context={"request": request}
        )
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        """Update group conversation title."""
        conversation = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = ConversationService.update_title(
            conversation=conversation,
            user=request.user,
            new_title=serializer.validated_data["title"],
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = ConversationDetailSerializer(
            result.data, context={"request": request}
        )
        return Response(output_serializer.data)

    def destroy(self, request, pk=None):
        """Soft delete a group conversation."""
        conversation = self.get_object()

        result = ConversationService.delete_conversation(
            conversation=conversation,
            user=request.user,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        """Mark conversation as read."""
        conversation = self.get_object()

        result = MessageService.mark_as_read(
            conversation=conversation,
            user=request.user,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"status": "read"})

    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        """Leave the conversation."""
        conversation = self.get_object()

        result = ParticipantService.leave(
            conversation=conversation,
            user=request.user,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"status": "left"})

    @action(detail=True, methods=["post"], url_path="transfer-ownership")
    def transfer_ownership(self, request, pk=None):
        """Transfer group ownership to another participant."""
        conversation = self.get_object()

        user_id = request.data.get("user_id")
        if not user_id:
            return Response(
                {"error": "user_id is required", "error_code": "MISSING_USER_ID"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_owner = get_object_or_404(User, id=user_id)

        result = ParticipantService.transfer_ownership(
            conversation=conversation,
            new_owner=new_owner,
            current_owner=request.user,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"status": "transferred"})


class ParticipantViewSet(viewsets.ModelViewSet):
    """
    ViewSet for participant operations within a conversation.

    list:
        Get all active participants in the conversation.

    create:
        Add a participant to a group conversation.
        Requires admin or owner role.

    partial_update:
        Change a participant's role.
        Requires owner role.

    destroy:
        Remove a participant from the conversation.
        Requires appropriate permissions based on role hierarchy.
    """

    permission_classes = [IsAuthenticated, IsConversationParticipant]
    serializer_class = ParticipantSerializer

    def get_conversation(self):
        """Get the parent conversation from URL."""
        conversation_id = self.kwargs.get("conversation_pk")
        return get_object_or_404(
            Conversation.objects.filter(is_deleted=False),
            pk=conversation_id,
        )

    def get_queryset(self):
        """Filter to active participants in the conversation."""
        conversation = self.get_conversation()
        return (
            Participant.objects.filter(
                conversation=conversation,
                left_at__isnull=True,
            )
            .select_related("user__profile")
            .order_by("joined_at")
        )

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == "create":
            return ParticipantCreateSerializer
        if self.action in ("update", "partial_update"):
            return ParticipantUpdateSerializer
        return ParticipantSerializer

    def get_permissions(self):
        """Return permissions based on action."""
        if self.action == "create":
            return [
                IsAuthenticated(),
                IsConversationParticipant(),
                IsGroupConversation(),
                CanManageParticipants(),
            ]
        if self.action in ("update", "partial_update"):
            return [
                IsAuthenticated(),
                IsConversationParticipant(),
                IsGroupConversation(),
                IsConversationOwner(),
            ]
        if self.action == "destroy":
            return [
                IsAuthenticated(),
                IsConversationParticipant(),
                IsGroupConversation(),
                CanManageParticipants(),
            ]
        return [IsAuthenticated(), IsConversationParticipant()]

    def get_object(self):
        """Get participant and check permissions."""
        queryset = self.get_queryset()
        participant = get_object_or_404(queryset, pk=self.kwargs.get("pk"))

        # Check object permissions (passes conversation for context)
        for permission in self.get_permissions():
            if hasattr(permission, "has_object_permission"):
                if not permission.has_object_permission(
                    self.request, self, participant
                ):
                    self.permission_denied(
                        self.request,
                        message=getattr(permission, "message", None),
                    )

        return participant

    def create(self, request, conversation_pk=None):
        """Add a participant to the conversation."""
        conversation = self.get_conversation()

        # Check permissions manually since we need conversation object
        for permission in self.get_permissions():
            if hasattr(permission, "has_object_permission"):
                if not permission.has_object_permission(request, self, conversation):
                    return Response(
                        {"error": getattr(permission, "message", "Permission denied")},
                        status=status.HTTP_403_FORBIDDEN,
                    )

        serializer = ParticipantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_to_add = get_object_or_404(User, id=serializer.validated_data["user_id"])

        result = ParticipantService.add_participant(
            conversation=conversation,
            user_to_add=user_to_add,
            added_by=request.user,
            role=serializer.validated_data.get("role"),
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = ParticipantSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, conversation_pk=None, pk=None):
        """Change a participant's role."""
        conversation = self.get_conversation()
        participant = self.get_object()

        serializer = ParticipantUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = ParticipantService.change_role(
            conversation=conversation,
            user_to_change=participant.user,
            new_role=serializer.validated_data["role"],
            changed_by=request.user,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = ParticipantSerializer(result.data)
        return Response(output_serializer.data)

    def destroy(self, request, conversation_pk=None, pk=None):
        """Remove a participant from the conversation."""
        conversation = self.get_conversation()
        participant = self.get_object()

        result = ParticipantService.remove_participant(
            conversation=conversation,
            user_to_remove=participant.user,
            removed_by=request.user,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class MessageViewSet(viewsets.ModelViewSet):
    """
    ViewSet for message operations within a conversation.

    list:
        Get all messages in the conversation.
        Includes soft-deleted messages (content replaced with placeholder).
        Uses cursor pagination, oldest first.

    create:
        Send a message to the conversation.
        Supports optional threading via parent_id.

    destroy:
        Soft delete a message.
        Users can only delete their own messages.
    """

    permission_classes = [IsAuthenticated, IsConversationParticipant]
    pagination_class = MessageCursorPagination

    def get_conversation(self):
        """Get the parent conversation from URL."""
        conversation_id = self.kwargs.get("conversation_pk")
        return get_object_or_404(
            Conversation.objects.filter(is_deleted=False),
            pk=conversation_id,
        )

    def get_queryset(self):
        """Get all messages in the conversation."""
        conversation = self.get_conversation()
        return (
            Message.objects.filter(
                conversation=conversation,
            )
            .select_related(
                "sender__profile",
                "parent_message",
            )
            .order_by("created_at", "id")
        )

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == "create":
            return MessageCreateSerializer
        return MessageSerializer

    def get_permissions(self):
        """Return permissions based on action."""
        if self.action == "destroy":
            return [
                IsAuthenticated(),
                IsConversationParticipant(),
                CanModifyMessage(),
            ]
        return [IsAuthenticated(), IsConversationParticipant()]

    def get_object(self):
        """Get message and check permissions."""
        queryset = self.get_queryset()
        message = get_object_or_404(queryset, pk=self.kwargs.get("pk"))

        # Check object permissions
        for permission in self.get_permissions():
            if hasattr(permission, "has_object_permission"):
                if not permission.has_object_permission(self.request, self, message):
                    self.permission_denied(
                        self.request,
                        message=getattr(permission, "message", None),
                    )

        return message

    def list(self, request, conversation_pk=None):
        """Get messages with participant check."""
        conversation = self.get_conversation()

        # Check participant permission
        participant = conversation.get_active_participant_for_user(request.user)
        if not participant:
            return Response(
                {"error": "You are not a participant in this conversation"},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = MessageSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = MessageSerializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, conversation_pk=None):
        """Send a message."""
        conversation = self.get_conversation()

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = MessageService.send_message(
            conversation=conversation,
            sender=request.user,
            content=serializer.validated_data["content"],
            parent_message_id=serializer.validated_data.get("parent_id"),
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = MessageSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, conversation_pk=None, pk=None):
        """Soft delete a message."""
        message = self.get_object()

        result = MessageService.delete_message(
            message=message,
            user=request.user,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)
