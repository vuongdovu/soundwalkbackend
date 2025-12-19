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

from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
    BulkPresenceRequestSerializer,
    ConversationCreateSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    ConversationUpdateSerializer,
    MessageCreateSerializer,
    MessageEditHistorySerializer,
    MessageEditSerializer,
    MessageSearchResultSerializer,
    MessageSerializer,
    ParticipantCreateSerializer,
    ParticipantSerializer,
    ParticipantUpdateSerializer,
    PresenceSerializer,
    PresenceSetSerializer,
    ReactionCreateSerializer,
    ReactionSerializer,
    ReactionToggleResponseSerializer,
)
from chat.services import (
    ConversationService,
    MessageSearchService,
    MessageService,
    ParticipantService,
    PresenceService,
    ReactionService,
)

User = get_user_model()


@extend_schema_view(
    list=extend_schema(
        operation_id="list_conversations",
        summary="List conversations",
        tags=["Chat - Conversations"],
    ),
    create=extend_schema(
        operation_id="create_conversation",
        summary="Create conversation",
        tags=["Chat - Conversations"],
    ),
    retrieve=extend_schema(
        operation_id="get_conversation",
        summary="Get conversation",
        tags=["Chat - Conversations"],
    ),
    update=extend_schema(
        operation_id="replace_conversation",
        summary="Replace conversation",
        tags=["Chat - Conversations"],
    ),
    partial_update=extend_schema(
        operation_id="update_conversation",
        summary="Update conversation",
        tags=["Chat - Conversations"],
    ),
    destroy=extend_schema(
        operation_id="delete_conversation",
        summary="Delete conversation",
        tags=["Chat - Conversations"],
    ),
)
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

    @extend_schema(
        operation_id="mark_conversation_read",
        summary="Mark conversation as read",
        tags=["Chat - Conversations"],
    )
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

    @extend_schema(
        operation_id="leave_conversation",
        summary="Leave conversation",
        tags=["Chat - Conversations"],
    )
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

    @extend_schema(
        operation_id="transfer_conversation_ownership",
        summary="Transfer conversation ownership",
        tags=["Chat - Conversations"],
    )
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

    @extend_schema(
        operation_id="get_conversation_presence",
        summary="Get conversation presence",
        description=(
            "Get the presence status of all participants in this conversation. "
            "Returns a list of online/away users. Useful for showing who is "
            "currently active in a conversation."
        ),
        responses={
            200: OpenApiResponse(
                response=PresenceSerializer(many=True),
                description="List of presence statuses for conversation participants",
            ),
            403: OpenApiResponse(description="Not a participant in this conversation"),
            404: OpenApiResponse(description="Conversation not found"),
        },
        tags=["Chat - Presence"],
    )
    @action(detail=True, methods=["get"], url_path="presence")
    def presence(self, request, pk=None):
        """
        Get presence status of all participants in this conversation.

        Returns list of online/away users currently viewing this conversation.
        Only active participants can view conversation presence.
        """
        conversation = self.get_object()

        # Check if user is a participant (already done by permission class)
        result = PresenceService.get_conversation_presence(conversation.id)

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(PresenceSerializer(result.data, many=True).data)


@extend_schema_view(
    list=extend_schema(
        operation_id="list_participants",
        summary="List participants",
        tags=["Chat - Participants"],
    ),
    create=extend_schema(
        operation_id="add_participant",
        summary="Add participant",
        tags=["Chat - Participants"],
    ),
    retrieve=extend_schema(
        operation_id="get_participant",
        summary="Get participant",
        tags=["Chat - Participants"],
    ),
    update=extend_schema(
        operation_id="replace_participant",
        summary="Replace participant",
        tags=["Chat - Participants"],
    ),
    partial_update=extend_schema(
        operation_id="update_participant_role",
        summary="Update participant role",
        tags=["Chat - Participants"],
    ),
    destroy=extend_schema(
        operation_id="remove_participant",
        summary="Remove participant",
        tags=["Chat - Participants"],
    ),
)
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


@extend_schema_view(
    list=extend_schema(
        operation_id="list_messages",
        summary="List messages",
        tags=["Chat - Messages"],
    ),
    create=extend_schema(
        operation_id="send_message",
        summary="Send message",
        tags=["Chat - Messages"],
    ),
    retrieve=extend_schema(
        operation_id="get_message",
        summary="Get message",
        tags=["Chat - Messages"],
    ),
    destroy=extend_schema(
        operation_id="delete_message",
        summary="Delete message",
        tags=["Chat - Messages"],
    ),
)
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

    @extend_schema(
        operation_id="edit_message",
        summary="Edit message",
        description=(
            "Edit the content of a message you sent. Edits are allowed within a "
            "configurable time window (default: 15 minutes). The original content is "
            "preserved in edit history and the message is marked as edited."
        ),
        request=MessageEditSerializer,
        responses={
            200: OpenApiResponse(
                response=MessageSerializer,
                description="Message edited successfully with updated content and edit metadata",
            ),
            400: OpenApiResponse(
                description="Edit not allowed (time window expired, empty content, or content unchanged)",
            ),
            403: OpenApiResponse(
                description="Cannot edit messages from other users",
            ),
            404: OpenApiResponse(description="Message not found"),
        },
        tags=["Chat - Messages"],
    )
    @action(detail=True, methods=["patch"])
    def edit(self, request, conversation_pk=None, pk=None):
        """
        Edit a message within the allowed time window.

        PATCH /api/v1/chat/conversations/{conversation_id}/messages/{id}/edit/
        """
        message = self.get_object()

        serializer = MessageEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = MessageService.edit_message(
            user=request.user,
            message_id=message.id,
            new_content=serializer.validated_data["content"],
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = MessageSerializer(result.data)
        return Response(output_serializer.data)

    @extend_schema(
        operation_id="get_message_edit_history",
        summary="Get message edit history",
        description=(
            "Retrieve the complete edit history for a message. Returns all previous "
            "versions of the message content, ordered by edit number. Only participants "
            "in the conversation can view edit history."
        ),
        responses={
            200: OpenApiResponse(
                response=MessageEditHistorySerializer(many=True),
                description="List of edit history entries with previous content and timestamps",
            ),
            403: OpenApiResponse(
                description="Not a participant in this conversation",
            ),
            404: OpenApiResponse(description="Message not found"),
        },
        tags=["Chat - Messages"],
    )
    @action(detail=True, methods=["get"])
    def history(self, request, conversation_pk=None, pk=None):
        """
        Get edit history for a message.

        GET /api/v1/chat/conversations/{conversation_id}/messages/{id}/history/
        """
        message = self.get_object()

        result = MessageService.get_edit_history(
            user=request.user,
            message_id=message.id,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MessageEditHistorySerializer(result.data, many=True)
        return Response(serializer.data)

    @extend_schema(
        methods=["GET"],
        operation_id="list_message_reactions",
        summary="List message reactions",
        description=(
            "Get all reactions on a message grouped by emoji. Returns the count "
            "of each emoji and the list of users who reacted with it."
        ),
        responses={
            200: OpenApiResponse(
                description="Reaction counts by emoji with user lists",
            ),
            403: OpenApiResponse(description="Not a participant in this conversation"),
            404: OpenApiResponse(description="Message not found"),
        },
        tags=["Chat - Reactions"],
    )
    @extend_schema(
        methods=["POST"],
        operation_id="add_reaction",
        summary="Add reaction to message",
        description=(
            "Add an emoji reaction to a message. Each user can only add one "
            "reaction per emoji per message. Use toggle endpoint for add/remove behavior."
        ),
        request=ReactionCreateSerializer,
        responses={
            201: OpenApiResponse(
                response=ReactionSerializer,
                description="Reaction added successfully",
            ),
            400: OpenApiResponse(
                description="Invalid emoji or reaction already exists"
            ),
            403: OpenApiResponse(description="Not a participant in this conversation"),
            404: OpenApiResponse(description="Message not found"),
        },
        tags=["Chat - Reactions"],
    )
    @action(detail=True, methods=["get", "post"], url_path="reactions")
    def reactions(self, request, conversation_pk=None, pk=None):
        """
        Get or add reactions to a message.

        GET /api/v1/chat/conversations/{conversation_id}/messages/{id}/reactions/
        POST /api/v1/chat/conversations/{conversation_id}/messages/{id}/reactions/
        """
        message = self.get_object()

        if request.method == "GET":
            result = ReactionService.get_message_reactions(message_id=message.id)

            if not result.success:
                return Response(
                    {"error": result.error, "error_code": result.error_code},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(result.data)

        # POST - add reaction
        serializer = ReactionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = ReactionService.add_reaction(
            user=request.user,
            message_id=message.id,
            emoji=serializer.validated_data["emoji"],
        )

        if not result.success:
            if result.error_code == "NOT_PARTICIPANT":
                return Response(
                    {"error": result.error, "error_code": result.error_code},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = ReactionSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        operation_id="remove_reaction",
        summary="Remove reaction from message",
        description=(
            "Remove your emoji reaction from a message. Only the user who added "
            "the reaction can remove it. The emoji must be URL-encoded if it contains "
            "special characters."
        ),
        parameters=[
            OpenApiParameter(
                name="emoji",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="The emoji to remove (URL-encoded)",
            ),
        ],
        responses={
            204: OpenApiResponse(description="Reaction removed successfully"),
            403: OpenApiResponse(description="Not a participant in this conversation"),
            404: OpenApiResponse(description="Message or reaction not found"),
        },
        tags=["Chat - Reactions"],
    )
    @action(detail=True, methods=["delete"], url_path="reactions/(?P<emoji>[^/.]+)")
    def remove_reaction(self, request, conversation_pk=None, pk=None, emoji=None):
        """
        Remove a reaction from a message.

        DELETE /api/v1/chat/conversations/{conversation_id}/messages/{id}/reactions/{emoji}/
        """
        message = self.get_object()

        # URL decode the emoji
        emoji = unquote(emoji)

        result = ReactionService.remove_reaction(
            user=request.user,
            message_id=message.id,
            emoji=emoji,
        )

        if not result.success:
            if result.error_code == "NOT_PARTICIPANT":
                return Response(
                    {"error": result.error, "error_code": result.error_code},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if result.error_code == "REACTION_NOT_FOUND":
                return Response(
                    {"error": result.error, "error_code": result.error_code},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        operation_id="toggle_reaction",
        summary="Toggle reaction on message",
        description=(
            "Toggle an emoji reaction on a message. If the reaction exists, it will be "
            "removed; if it doesn't exist, it will be added. This is the recommended "
            "endpoint for implementing reaction buttons in the UI."
        ),
        request=ReactionCreateSerializer,
        responses={
            200: OpenApiResponse(
                response=ReactionToggleResponseSerializer,
                description="Toggle result with added flag and reaction data",
            ),
            403: OpenApiResponse(description="Not a participant in this conversation"),
            404: OpenApiResponse(description="Message not found"),
        },
        tags=["Chat - Reactions"],
    )
    @action(detail=True, methods=["post"], url_path="reactions/toggle")
    def toggle_reaction(self, request, conversation_pk=None, pk=None):
        """
        Toggle a reaction on a message.

        POST /api/v1/chat/conversations/{conversation_id}/messages/{id}/reactions/toggle/
        """
        message = self.get_object()

        serializer = ReactionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = ReactionService.toggle_reaction(
            user=request.user,
            message_id=message.id,
            emoji=serializer.validated_data["emoji"],
        )

        if not result.success:
            if result.error_code == "NOT_PARTICIPANT":
                return Response(
                    {"error": result.error, "error_code": result.error_code},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        added, reaction = result.data
        response_data = {
            "added": added,
            "reaction": ReactionSerializer(reaction).data if reaction else None,
        }
        return Response(response_data)


# =============================================================================
# Message Search
# =============================================================================


class MessageSearchView(APIView):
    """
    Search messages across user's conversations.

    GET /api/v1/chat/messages/search/?q=query&conversation_id=X&cursor=Y&page_size=Z

    Query parameters:
        q: Search query (required, min 2 characters)
        conversation_id: Optional - limit search to specific conversation
        cursor: Optional - pagination cursor from previous search
        page_size: Optional - number of results per page (default 20, max 100)
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="search_messages",
        summary="Search messages",
        description=(
            "Full-text search across all messages in conversations where the user is a "
            "participant. Uses PostgreSQL full-text search with relevance ranking. "
            "Results can be filtered to a specific conversation and are paginated using cursors."
        ),
        parameters=[
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query (required, minimum 2 characters)",
                required=True,
            ),
            OpenApiParameter(
                name="conversation_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Limit search to a specific conversation",
                required=False,
            ),
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Pagination cursor from previous search results",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page (default 20, max 100)",
                required=False,
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="Search results with messages, next cursor, and has_more flag",
            ),
            400: OpenApiResponse(
                description="Invalid query (missing, too short, or invalid conversation_id)",
            ),
        },
        tags=["Chat - Search"],
    )
    def get(self, request):
        """Search for messages."""
        query = request.query_params.get("q")
        conversation_id = request.query_params.get("conversation_id")
        cursor = request.query_params.get("cursor")
        page_size = request.query_params.get("page_size", 20)

        # Validate query parameter
        if not query:
            return Response(
                {"error": "Query parameter 'q' is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Convert conversation_id to int if provided
        if conversation_id:
            try:
                conversation_id = int(conversation_id)
            except ValueError:
                return Response(
                    {"error": "conversation_id must be an integer"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Convert page_size to int
        try:
            page_size = int(page_size)
        except ValueError:
            page_size = 20

        # Perform search
        result = MessageSearchService.search(
            user=request.user,
            query=query,
            conversation_id=conversation_id,
            cursor=cursor,
            page_size=page_size,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Serialize results
        serialized_results = MessageSearchResultSerializer(
            result.data["results"], many=True
        ).data

        return Response(
            {
                "results": serialized_results,
                "next_cursor": result.data["next_cursor"],
                "has_more": result.data["has_more"],
            }
        )


# =============================================================================
# Presence Views
# =============================================================================


class PresenceView(APIView):
    """
    Manage current user's presence.

    POST /api/v1/chat/presence/
        Set user presence status.

    Payload:
        status: "online" | "away" | "offline"
        conversation_id: Optional - specific conversation context
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="set_presence",
        summary="Set presence status",
        description=(
            "Set the current user's presence status. Status is stored in Redis with "
            "automatic expiration. Optionally specify a conversation ID to indicate "
            "which conversation the user is currently viewing."
        ),
        request=PresenceSetSerializer,
        responses={
            200: OpenApiResponse(
                response=PresenceSerializer,
                description="Presence status updated successfully",
            ),
            400: OpenApiResponse(description="Invalid status value"),
        },
        tags=["Chat - Presence"],
    )
    def post(self, request):
        """Set current user's presence status."""
        serializer = PresenceSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = PresenceService.set_presence(
            user_id=request.user.id,
            status=serializer.validated_data["status"],
            conversation_id=serializer.validated_data.get("conversation_id"),
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(PresenceSerializer(result.data).data)


class UserPresenceView(APIView):
    """
    Get presence status for a specific user.

    GET /api/v1/chat/presence/{user_id}/
        Get user's presence status.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="get_user_presence",
        summary="Get user presence",
        description=(
            "Get the presence status for a specific user. Returns current status "
            "(online, away, offline) and last seen timestamp. Useful for showing "
            "online indicators in user lists."
        ),
        parameters=[
            OpenApiParameter(
                name="user_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="UUID of the user to query",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=PresenceSerializer,
                description="User's presence status",
            ),
        },
        tags=["Chat - Presence"],
    )
    def get(self, request, user_id):
        """Get presence for a specific user."""
        result = PresenceService.get_presence(user_id)

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(PresenceSerializer(result.data).data)


class BulkPresenceView(APIView):
    """
    Get presence status for multiple users.

    POST /api/v1/chat/presence/bulk/
        Get presence for multiple users.

    Payload:
        user_ids: List of user IDs to query
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="get_bulk_presence",
        summary="Get presence for multiple users",
        description=(
            "Get presence status for multiple users in a single request. Useful for "
            "populating online indicators when loading a conversation participant list. "
            "Limited to 100 user IDs per request."
        ),
        request=BulkPresenceRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=PresenceSerializer(many=True),
                description="List of presence statuses for requested users",
            ),
            400: OpenApiResponse(
                description="Invalid user IDs or too many IDs requested"
            ),
        },
        tags=["Chat - Presence"],
    )
    def post(self, request):
        """Get bulk presence for multiple users."""
        serializer = BulkPresenceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = PresenceService.get_bulk_presence(
            serializer.validated_data["user_ids"]
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(PresenceSerializer(result.data, many=True).data)


class HeartbeatView(APIView):
    """
    Send heartbeat to keep presence alive.

    POST /api/v1/chat/presence/heartbeat/
        Update last_seen and extend TTL.

    Payload (optional):
        conversation_id: Specific conversation context
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="send_heartbeat",
        summary="Send presence heartbeat",
        description=(
            "Send a heartbeat to keep the user's presence status alive. Should be called "
            "periodically (every 30-60 seconds) while the user has the app open. Extends "
            "the TTL on the presence record and updates last_seen timestamp."
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "integer",
                        "description": "Optional conversation the user is currently viewing",
                    },
                },
            }
        },
        responses={
            200: OpenApiResponse(
                response=PresenceSerializer,
                description="Heartbeat processed, presence TTL extended",
            ),
        },
        tags=["Chat - Presence"],
    )
    def post(self, request):
        """Process heartbeat for current user."""
        conversation_id = request.data.get("conversation_id")

        result = PresenceService.heartbeat(
            user_id=request.user.id,
            conversation_id=conversation_id,
        )

        if not result.success:
            return Response(
                {"error": result.error, "error_code": result.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(PresenceSerializer(result.data).data)
