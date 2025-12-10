"""
DRF serializers for chat app.

This module provides serializers for:
- Conversation listing and detail
- Message display
- Participant management

Related files:
    - models.py: Conversation, Message models
    - views.py: Chat API views

Usage:
    conversations = user.conversations.all()
    serializer = ConversationSerializer(conversations, many=True)
"""

from __future__ import annotations

from rest_framework import serializers


class ParticipantSerializer(serializers.Serializer):
    """
    Serializer for conversation participants.

    Fields:
        id: User ID
        email: User email
        first_name: User's first name
        last_name: User's last name
        avatar_url: Profile avatar URL
        role: Participant role
        is_online: Whether user is currently online
    """

    # TODO: Implement serializer fields
    # id = serializers.IntegerField(read_only=True)
    # email = serializers.EmailField(read_only=True)
    # first_name = serializers.CharField(read_only=True)
    # last_name = serializers.CharField(read_only=True)
    # avatar_url = serializers.URLField(read_only=True, source="profile.avatar_url")
    # role = serializers.CharField(read_only=True, source="participation.role")
    # is_online = serializers.SerializerMethodField()
    #
    # def get_is_online(self, obj) -> bool:
    #     """Check if user is currently online."""
    #     # TODO: Implement presence check
    #     return False
    pass


class MessageSerializer(serializers.Serializer):
    """
    Serializer for chat messages.

    Fields:
        id: Message ID
        conversation_id: Parent conversation
        sender: Sender info (nested)
        message_type: Type of message
        content: Message content
        metadata: Additional data
        reply_to: Parent message ID
        is_edited: Whether edited
        created_at: Creation time

    Usage:
        messages = conversation.messages.all()[:50]
        serializer = MessageSerializer(messages, many=True)
    """

    # TODO: Implement serializer fields
    # id = serializers.IntegerField(read_only=True)
    # conversation_id = serializers.IntegerField(read_only=True)
    # sender = ParticipantSerializer(read_only=True)
    # message_type = serializers.CharField(read_only=True)
    # content = serializers.CharField(read_only=True)
    # metadata = serializers.JSONField(read_only=True)
    # reply_to = serializers.IntegerField(read_only=True, source="reply_to_id")
    # is_edited = serializers.BooleanField(read_only=True)
    # created_at = serializers.DateTimeField(read_only=True)
    pass


class ConversationSerializer(serializers.Serializer):
    """
    Serializer for conversations.

    Fields:
        id: Conversation ID
        conversation_type: Type (direct/group/ai)
        name: Conversation name
        participants: List of participants
        last_message: Most recent message
        unread_count: Unread messages for current user
        last_message_at: Time of last message
        is_muted: Whether muted by current user

    Usage:
        conversations = user.conversations.all()
        serializer = ConversationSerializer(
            conversations,
            many=True,
            context={"request": request}
        )
    """

    # TODO: Implement serializer fields
    # id = serializers.IntegerField(read_only=True)
    # conversation_type = serializers.CharField(read_only=True)
    # name = serializers.SerializerMethodField()
    # participants = ParticipantSerializer(many=True, read_only=True)
    # last_message = MessageSerializer(read_only=True)
    # unread_count = serializers.SerializerMethodField()
    # last_message_at = serializers.DateTimeField(read_only=True)
    # is_muted = serializers.SerializerMethodField()
    #
    # def get_name(self, obj) -> str:
    #     """Get conversation name (or other participant name for direct)."""
    #     if obj.name:
    #         return obj.name
    #     if obj.is_direct:
    #         user = self.context["request"].user
    #         other = obj.get_other_participant(user)
    #         return other.first_name or other.email if other else "Unknown"
    #     return f"Conversation {obj.id}"
    #
    # def get_unread_count(self, obj) -> int:
    #     """Get unread message count for current user."""
    #     user = self.context["request"].user
    #     participation = obj.participations.filter(user=user).first()
    #     if not participation:
    #         return 0
    #     return obj.messages.filter(
    #         id__gt=participation.last_read_message_id or 0,
    #         is_deleted=False,
    #     ).exclude(sender=user).count()
    #
    # def get_is_muted(self, obj) -> bool:
    #     """Check if conversation is muted by current user."""
    #     user = self.context["request"].user
    #     participation = obj.participations.filter(user=user).first()
    #     return participation.is_muted_now if participation else False
    pass


class CreateConversationSerializer(serializers.Serializer):
    """
    Serializer for creating conversations.

    Fields:
        conversation_type: Type of conversation
        participant_ids: List of participant user IDs
        name: Optional name for group chats
        metadata: Optional additional data

    Usage:
        serializer = CreateConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = ChatService.create_conversation(
            creator=request.user,
            participants=serializer.validated_data["participants"],
            **serializer.validated_data
        )
    """

    conversation_type = serializers.ChoiceField(
        choices=["direct", "group", "ai"],
        default="direct",
    )
    participant_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        help_text="User IDs to add to conversation",
    )
    name = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text="Name for group conversations",
    )
    metadata = serializers.JSONField(
        required=False,
        default=dict,
        help_text="Additional conversation metadata",
    )


class SendMessageSerializer(serializers.Serializer):
    """
    Serializer for sending messages.

    Fields:
        content: Message content
        message_type: Type of message
        reply_to: Optional parent message ID
        metadata: Optional additional data

    Usage:
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = ChatService.send_message(
            conversation=conversation,
            sender=request.user,
            **serializer.validated_data
        )
    """

    content = serializers.CharField(
        max_length=10000,
        help_text="Message content",
    )
    message_type = serializers.ChoiceField(
        choices=["text", "image", "file"],
        default="text",
    )
    reply_to = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="ID of message being replied to",
    )
    metadata = serializers.JSONField(
        required=False,
        default=dict,
        help_text="Additional message metadata (file info, etc.)",
    )


class MarkAsReadSerializer(serializers.Serializer):
    """
    Serializer for marking messages as read.

    Fields:
        message_id: ID of last read message

    Usage:
        serializer = MarkAsReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ChatService.mark_as_read(
            conversation=conversation,
            user=request.user,
            message_id=serializer.validated_data["message_id"]
        )
    """

    message_id = serializers.IntegerField(
        help_text="ID of last read message",
    )
