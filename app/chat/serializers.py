"""
Serializers for chat API.

This module provides serializers for the chat system:
- Conversation serializers (list, detail, create, update)
- Participant serializers (read, create, update)
- Message serializers (read, create)

Serializer Hierarchy:
    ConversationListSerializer: List view with computed fields
    ConversationDetailSerializer: Full details including participants
    ConversationCreateSerializer: Direct/group conversation creation
    ConversationUpdateSerializer: Group title update

    ParticipantSerializer: Participant with user info
    ParticipantCreateSerializer: Add participant to group
    ParticipantUpdateSerializer: Change participant role

    MessageSerializer: Message with soft-delete handling
    MessageCreateSerializer: Send new message
    MessagePreviewSerializer: Minimal message for list preview

Design Decisions:
    - Read and write serializers are separate for clarity
    - Soft-deleted message content is replaced with placeholder
    - System messages show formatted event description
    - Computed fields use SerializerMethodField for flexibility
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from rest_framework import serializers

from authentication.serializers import UserSerializer
from chat.models import (
    Conversation,
    ConversationType,
    Message,
    Participant,
    ParticipantRole,
    SystemMessageEvent,
)

if TYPE_CHECKING:
    from authentication.models import User

User = get_user_model()


# =============================================================================
# Helper Functions
# =============================================================================


def format_system_message(content: str) -> str:
    """
    Format system message content for display.

    Converts JSON system message content into human-readable text.

    Args:
        content: JSON string with {event, data}

    Returns:
        Human-readable message string
    """
    try:
        data = json.loads(content)
        event = data.get("event", "")
        event_data = data.get("data", {})

        formatters = {
            SystemMessageEvent.GROUP_CREATED: lambda d: f'Group "{d.get("title", "")}" was created',
            SystemMessageEvent.PARTICIPANT_ADDED: lambda d: "A participant was added to the group",
            SystemMessageEvent.PARTICIPANT_REMOVED: lambda d: (
                "A participant left the group"
                if d.get("reason") == "left"
                else "A participant was removed from the group"
            ),
            SystemMessageEvent.ROLE_CHANGED: lambda d: f"A participant's role was changed to {d.get('new_role', 'unknown')}",
            SystemMessageEvent.OWNERSHIP_TRANSFERRED: lambda d: "Group ownership was transferred",
            SystemMessageEvent.TITLE_CHANGED: lambda d: f'Group title was changed to "{d.get("new_title", "")}"',
        }

        formatter = formatters.get(event)
        if formatter:
            return formatter(event_data)
        return "System message"

    except (json.JSONDecodeError, TypeError, KeyError):
        return "System message"


# =============================================================================
# Message Serializers
# =============================================================================


class MessagePreviewSerializer(serializers.ModelSerializer):
    """
    Minimal message serializer for conversation list preview.

    Used to show the last message in conversation lists.
    Handles soft-deleted message content replacement.
    """

    sender_name = serializers.SerializerMethodField(
        help_text="Display name of the message sender"
    )
    content = serializers.SerializerMethodField(
        help_text="Message content (replaced if deleted)"
    )

    class Meta:
        model = Message
        fields = [
            "id",
            "sender_name",
            "content",
            "message_type",
            "created_at",
        ]
        read_only_fields = fields

    def get_sender_name(self, obj: Message) -> str | None:
        """Get sender's display name or None for system messages."""
        if obj.sender is None:
            return None
        return obj.sender.get_full_name() or obj.sender.email

    def get_content(self, obj: Message) -> str:
        """
        Get display content.

        - Deleted messages: "[Message deleted]"
        - System messages: Formatted event description
        - Text messages: Original content
        """
        if obj.is_deleted:
            return "[Message deleted]"
        if obj.is_system_message:
            return format_system_message(obj.content)
        return obj.content


class MessageSerializer(serializers.ModelSerializer):
    """
    Full message serializer for message lists.

    Includes sender details, threading information, and proper
    handling of deleted/system messages.
    """

    sender = UserSerializer(read_only=True, allow_null=True)
    content = serializers.SerializerMethodField(
        help_text="Message content (replaced if deleted)"
    )
    is_deleted = serializers.BooleanField(read_only=True)
    parent_id = serializers.IntegerField(
        source="parent_message_id",
        read_only=True,
        allow_null=True,
    )
    reply_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation_id",
            "sender",
            "content",
            "message_type",
            "is_deleted",
            "parent_id",
            "reply_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_content(self, obj: Message) -> str:
        """
        Get display content.

        - Deleted messages: "[Message deleted]"
        - System messages: Formatted event description
        - Text messages: Original content
        """
        if obj.is_deleted:
            return "[Message deleted]"
        if obj.is_system_message:
            return format_system_message(obj.content)
        return obj.content


class MessageCreateSerializer(serializers.Serializer):
    """
    Serializer for sending messages.

    Supports:
    - Regular text messages
    - Replies (with parent_id for threading)
    """

    content = serializers.CharField(
        max_length=10000,
        help_text="Message content (max 10,000 characters)",
    )
    parent_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Parent message ID for threading (optional)",
    )


# =============================================================================
# Participant Serializers
# =============================================================================


class ParticipantSerializer(serializers.ModelSerializer):
    """
    Read serializer for conversation participants.

    Includes user details and role information.
    """

    user = UserSerializer(read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Participant
        fields = [
            "id",
            "user",
            "role",
            "joined_at",
            "last_read_at",
            "is_active",
        ]
        read_only_fields = fields


class ParticipantCreateSerializer(serializers.Serializer):
    """
    Serializer for adding participants to group conversations.

    Validates user exists and is active.
    Prevents assigning OWNER role through this endpoint.
    """

    user_id = serializers.UUIDField(help_text="User ID to add to conversation")
    role = serializers.ChoiceField(
        choices=[
            (ParticipantRole.ADMIN, "Admin"),
            (ParticipantRole.MEMBER, "Member"),
        ],
        default=ParticipantRole.MEMBER,
        help_text="Role for the new participant (admin or member)",
    )

    def validate_user_id(self, value) -> "uuid.UUID":
        """Ensure user exists and is active."""
        try:
            User.objects.get(id=value, is_active=True)
        except User.DoesNotExist:
            raise serializers.ValidationError("User not found or inactive")
        return value


class ParticipantUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating participant role.

    Only allows changing to ADMIN or MEMBER roles.
    OWNER role must use transfer_ownership endpoint.
    """

    role = serializers.ChoiceField(
        choices=[
            (ParticipantRole.ADMIN, "Admin"),
            (ParticipantRole.MEMBER, "Member"),
        ],
        help_text="New role for the participant (admin or member)",
    )


# =============================================================================
# Conversation Serializers
# =============================================================================


class ConversationListSerializer(serializers.ModelSerializer):
    """
    Serializer for conversation list view.

    Includes computed fields:
    - unread_count: Number of unread messages for current user
    - last_message: Preview of most recent message
    - display_name: Title for groups, other user's name for direct
    - other_participants: Other users in conversation (for display)
    """

    unread_count = serializers.SerializerMethodField(
        help_text="Number of unread messages"
    )
    last_message = serializers.SerializerMethodField(
        help_text="Most recent message preview"
    )
    display_name = serializers.SerializerMethodField(
        help_text="Display name for the conversation"
    )
    other_participants = serializers.SerializerMethodField(
        help_text="Other participants in the conversation"
    )

    class Meta:
        model = Conversation
        fields = [
            "id",
            "conversation_type",
            "title",
            "display_name",
            "participant_count",
            "other_participants",
            "unread_count",
            "last_message",
            "last_message_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_unread_count(self, obj: Conversation) -> int:
        """Count messages after user's last_read_at."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return 0

        user = request.user
        participant = obj.participants.filter(user=user, left_at__isnull=True).first()

        if not participant:
            return 0

        queryset = obj.messages.exclude(sender=user)

        if participant.last_read_at:
            queryset = queryset.filter(created_at__gt=participant.last_read_at)

        return queryset.count()

    def get_last_message(self, obj: Conversation) -> dict | None:
        """Get most recent message preview."""
        last_message = obj.messages.order_by("-created_at").first()
        if last_message:
            return MessagePreviewSerializer(last_message).data
        return None

    def get_display_name(self, obj: Conversation) -> str:
        """
        Generate display name for conversation.

        - Groups: title
        - Direct: other user's name
        """
        if obj.title:
            return obj.title

        if obj.conversation_type == ConversationType.DIRECT:
            request = self.context.get("request")
            if request and request.user.is_authenticated:
                other = (
                    obj.participants.exclude(user=request.user)
                    .select_related("user")
                    .first()
                )
                if other:
                    return other.user.get_full_name() or other.user.email

        return f"Group ({obj.participant_count} members)"

    def get_other_participants(self, obj: Conversation) -> list[dict]:
        """Get list of other participant info (for display)."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return []

        others = (
            obj.participants.filter(left_at__isnull=True)
            .exclude(user=request.user)
            .select_related("user__profile")[:5]
        )  # Limit for performance

        result = []
        for p in others:
            user = p.user
            profile = getattr(user, "profile", None)
            result.append(
                {
                    "id": user.id,
                    "name": user.get_full_name() or user.email,
                    "username": profile.username if profile else None,
                }
            )
        return result


class ConversationDetailSerializer(ConversationListSerializer):
    """
    Full conversation details including all participants.

    Extends ConversationListSerializer with participant list and
    current user's role.
    """

    participants = serializers.SerializerMethodField(
        help_text="All active participants"
    )
    current_user_role = serializers.SerializerMethodField(
        help_text="Current user's role in this conversation"
    )

    class Meta(ConversationListSerializer.Meta):
        fields = ConversationListSerializer.Meta.fields + [
            "participants",
            "current_user_role",
        ]

    def get_participants(self, obj: Conversation) -> list[dict]:
        """Get all active participants."""
        participants = (
            obj.participants.filter(left_at__isnull=True)
            .select_related("user__profile")
            .order_by("joined_at")
        )
        return ParticipantSerializer(participants, many=True).data

    def get_current_user_role(self, obj: Conversation) -> str | None:
        """Get current user's role in conversation."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None

        participant = obj.participants.filter(
            user=request.user,
            left_at__isnull=True,
        ).first()

        return participant.role if participant else None


class ConversationCreateSerializer(serializers.Serializer):
    """
    Serializer for creating conversations.

    Supports both direct (1:1) and group conversations:
    - Direct: Finds existing or creates new between two users
    - Group: Creates new group with specified participants
    """

    conversation_type = serializers.ChoiceField(
        choices=ConversationType.choices,
        help_text="Type of conversation to create",
    )
    participant_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        help_text="List of user IDs to include in conversation",
    )
    title = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="",
        help_text="Title for group conversations (ignored for direct)",
    )

    def validate(self, attrs: dict) -> dict:
        """Validate based on conversation type."""
        conv_type = attrs["conversation_type"]
        participant_ids = attrs["participant_ids"]
        title = attrs.get("title", "").strip()

        # Direct conversations require exactly one other participant
        if conv_type == ConversationType.DIRECT:
            if len(participant_ids) != 1:
                raise serializers.ValidationError(
                    {
                        "participant_ids": "Direct conversations require exactly one other participant"
                    }
                )
            if title:
                raise serializers.ValidationError(
                    {"title": "Direct conversations cannot have a title"}
                )

        # Group conversations need a title
        if conv_type == ConversationType.GROUP:
            if not title:
                raise serializers.ValidationError(
                    {"title": "Group conversations require a title"}
                )

        return attrs

    def validate_participant_ids(self, value: list[int]) -> list[int]:
        """Ensure all participant IDs are valid users."""
        request = self.context.get("request")
        if not request:
            return value

        # Check current user is not in the list
        user = request.user
        if user.id in value:
            raise serializers.ValidationError(
                "Cannot include yourself in participant list"
            )

        # Check all users exist and are active
        existing = User.objects.filter(id__in=value, is_active=True).values_list(
            "id", flat=True
        )
        existing_set = set(existing)
        invalid = [uid for uid in value if uid not in existing_set]

        if invalid:
            raise serializers.ValidationError(f"Users not found or inactive: {invalid}")

        return value


class ConversationUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating group conversation.

    Only title can be updated.
    """

    title = serializers.CharField(
        max_length=100,
        help_text="New title for the group",
    )

    def validate_title(self, value: str) -> str:
        """Ensure title is not empty."""
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Title cannot be empty")
        return value
