"""
Chat models for real-time messaging.

This module defines models for:
- Conversation: Chat room/thread
- ConversationParticipant: User membership in conversations
- Message: Individual messages
- MessageReadReceipt: Read tracking per user

Related files:
    - services.py: ChatService for business logic
    - consumers.py: WebSocket handlers
    - tasks.py: Async message processing

Model Relationships:
    User (*) <---> (*) Conversation (through ConversationParticipant)
    Conversation (1) ---> (*) Message
    Message (1) ---> (*) MessageReadReceipt
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from core.models import BaseModel

if TYPE_CHECKING:
    pass


class ConversationType(models.TextChoices):
    """Types of conversations."""

    DIRECT = "direct", "Direct Message"
    GROUP = "group", "Group Chat"
    AI = "ai", "AI Chat"


class ParticipantRole(models.TextChoices):
    """Participant roles in conversations."""

    MEMBER = "member", "Member"
    ADMIN = "admin", "Admin"
    OWNER = "owner", "Owner"


class MessageType(models.TextChoices):
    """Types of messages."""

    TEXT = "text", "Text"
    IMAGE = "image", "Image"
    FILE = "file", "File"
    SYSTEM = "system", "System"
    AI = "ai", "AI Response"


class Conversation(BaseModel):
    """
    Chat conversation (room/thread).

    Represents a chat context where participants can
    exchange messages. Supports direct, group, and AI chats.

    Fields:
        conversation_type: Type of conversation
        name: Name for group chats (blank for direct)
        participants: Users in conversation (ManyToMany through)
        metadata: Additional data (for AI: model, system_prompt)
        is_archived: Whether conversation is archived
        last_message_at: Time of last message (for sorting)

    Indexes:
        - [is_archived, last_message_at] for listing
        - conversation_type

    Usage:
        conversations = Conversation.objects.filter(
            participants=user,
            is_archived=False
        ).order_by("-last_message_at")
    """

    # TODO: Implement model fields
    # conversation_type = models.CharField(
    #     max_length=10,
    #     choices=ConversationType.choices,
    #     default=ConversationType.DIRECT,
    # )
    # name = models.CharField(
    #     max_length=100,
    #     blank=True,
    #     help_text="Name for group chats",
    # )
    # participants = models.ManyToManyField(
    #     settings.AUTH_USER_MODEL,
    #     through="ConversationParticipant",
    #     related_name="conversations",
    # )
    # metadata = models.JSONField(
    #     default=dict,
    #     blank=True,
    #     help_text="AI config: model, system_prompt, temperature",
    # )
    # is_archived = models.BooleanField(default=False, db_index=True)
    # last_message_at = models.DateTimeField(
    #     null=True,
    #     blank=True,
    #     db_index=True,
    # )

    class Meta:
        verbose_name = "Conversation"
        verbose_name_plural = "Conversations"
        ordering = ["-last_message_at"]
        # indexes = [
        #     models.Index(fields=["is_archived", "-last_message_at"]),
        # ]

    def __str__(self) -> str:
        # TODO: Implement
        # if self.name:
        #     return self.name
        # return f"Conversation {self.id}"
        return "Conversation"

    # TODO: Implement properties
    # @property
    # def is_direct(self) -> bool:
    #     """Check if this is a direct conversation."""
    #     return self.conversation_type == ConversationType.DIRECT
    #
    # @property
    # def is_group(self) -> bool:
    #     """Check if this is a group conversation."""
    #     return self.conversation_type == ConversationType.GROUP
    #
    # @property
    # def is_ai(self) -> bool:
    #     """Check if this is an AI conversation."""
    #     return self.conversation_type == ConversationType.AI
    #
    # def get_other_participant(self, user):
    #     """Get the other participant in a direct conversation."""
    #     if not self.is_direct:
    #         return None
    #     return self.participants.exclude(id=user.id).first()
    #
    # def get_channel_name(self) -> str:
    #     """Get WebSocket channel group name."""
    #     return f"chat_{self.id}"


class ConversationParticipant(BaseModel):
    """
    User participation in a conversation.

    Tracks membership, roles, and read state for each
    user in a conversation.

    Fields:
        conversation: The conversation
        user: Participating user
        role: User's role (member/admin/owner)
        last_read_at: When user last viewed conversation
        last_read_message_id: Last message user has seen
        is_muted: Whether notifications are muted
        muted_until: Temporary mute end time
        is_active: Whether user is still in conversation
        left_at: When user left (if not active)

    Unique together:
        - [conversation, user]

    Usage:
        participant = ConversationParticipant.objects.get(
            conversation=conversation,
            user=user
        )
        unread_count = conversation.messages.filter(
            id__gt=participant.last_read_message_id or 0
        ).count()
    """

    # TODO: Implement model fields
    # conversation = models.ForeignKey(
    #     Conversation,
    #     on_delete=models.CASCADE,
    #     related_name="participations",
    # )
    # user = models.ForeignKey(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.CASCADE,
    #     related_name="conversation_participations",
    # )
    # role = models.CharField(
    #     max_length=10,
    #     choices=ParticipantRole.choices,
    #     default=ParticipantRole.MEMBER,
    # )
    # last_read_at = models.DateTimeField(null=True, blank=True)
    # last_read_message_id = models.BigIntegerField(null=True, blank=True)
    # is_muted = models.BooleanField(default=False)
    # muted_until = models.DateTimeField(null=True, blank=True)
    # is_active = models.BooleanField(
    #     default=True,
    #     help_text="False if user left the conversation",
    # )
    # left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Conversation Participant"
        verbose_name_plural = "Conversation Participants"
        # unique_together = [["conversation", "user"]]

    def __str__(self) -> str:
        # TODO: Implement
        # return f"{self.user.email} in {self.conversation}"
        return "ConversationParticipant"

    # TODO: Implement methods
    # def mark_as_read(self, message_id: int) -> None:
    #     """Mark conversation as read up to message."""
    #     from django.utils import timezone
    #     self.last_read_at = timezone.now()
    #     self.last_read_message_id = message_id
    #     self.save(update_fields=["last_read_at", "last_read_message_id", "updated_at"])
    #
    # @property
    # def is_muted_now(self) -> bool:
    #     """Check if currently muted."""
    #     if not self.is_muted:
    #         return False
    #     if self.muted_until:
    #         from django.utils import timezone
    #         return timezone.now() < self.muted_until
    #     return True


class Message(BaseModel):
    """
    Individual chat message.

    Stores message content and metadata for all message types.

    Fields:
        conversation: Parent conversation
        sender: Message author (null for system messages)
        message_type: Type of message
        content: Message text content
        metadata: Additional data (file info, AI tokens)
        reply_to: Parent message for replies
        is_edited: Whether message was edited
        edited_at: When message was last edited
        is_deleted: Whether message is soft-deleted
        deleted_at: When message was deleted

    Indexes:
        - [conversation, created_at]
        - [sender, created_at]

    Usage:
        messages = Message.objects.filter(
            conversation=conversation,
            is_deleted=False
        ).select_related("sender").order_by("created_at")
    """

    # TODO: Implement model fields
    # conversation = models.ForeignKey(
    #     Conversation,
    #     on_delete=models.CASCADE,
    #     related_name="messages",
    # )
    # sender = models.ForeignKey(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     related_name="sent_messages",
    # )
    # message_type = models.CharField(
    #     max_length=10,
    #     choices=MessageType.choices,
    #     default=MessageType.TEXT,
    # )
    # content = models.TextField()
    # metadata = models.JSONField(
    #     default=dict,
    #     blank=True,
    #     help_text="File: name, size, mime_type. AI: tokens_used",
    # )
    # reply_to = models.ForeignKey(
    #     "self",
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     blank=True,
    #     related_name="replies",
    # )
    # is_edited = models.BooleanField(default=False)
    # edited_at = models.DateTimeField(null=True, blank=True)
    # is_deleted = models.BooleanField(default=False)
    # deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Message"
        verbose_name_plural = "Messages"
        ordering = ["created_at"]
        # indexes = [
        #     models.Index(fields=["conversation", "created_at"]),
        #     models.Index(fields=["sender", "created_at"]),
        # ]

    def __str__(self) -> str:
        # TODO: Implement
        # sender_name = self.sender.email if self.sender else "System"
        # return f"{sender_name}: {self.content[:50]}"
        return "Message"

    # TODO: Implement methods
    # def soft_delete(self) -> None:
    #     """Soft delete the message."""
    #     from django.utils import timezone
    #     self.is_deleted = True
    #     self.deleted_at = timezone.now()
    #     self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
    #
    # def edit(self, new_content: str) -> None:
    #     """Edit message content."""
    #     from django.utils import timezone
    #     self.content = new_content
    #     self.is_edited = True
    #     self.edited_at = timezone.now()
    #     self.save(update_fields=["content", "is_edited", "edited_at", "updated_at"])
    #
    # @property
    # def content_preview(self) -> str:
    #     """Get truncated content preview."""
    #     if self.is_deleted:
    #         return "[Message deleted]"
    #     return self.content[:100] + ("..." if len(self.content) > 100 else "")
    #
    # def to_websocket_payload(self) -> dict:
    #     """Convert to WebSocket message payload."""
    #     return {
    #         "id": self.id,
    #         "conversation_id": self.conversation_id,
    #         "sender_id": self.sender_id,
    #         "sender_name": self.sender.first_name if self.sender else None,
    #         "message_type": self.message_type,
    #         "content": self.content if not self.is_deleted else "[Message deleted]",
    #         "metadata": self.metadata,
    #         "reply_to": self.reply_to_id,
    #         "is_edited": self.is_edited,
    #         "created_at": self.created_at.isoformat(),
    #     }


class MessageReadReceipt(BaseModel):
    """
    Read receipt for a message.

    Tracks when each user read each message.
    Used for "seen by" indicators.

    Fields:
        message: The message that was read
        user: User who read it
        read_at: When they read it (auto-set on create)

    Unique together:
        - [message, user]

    Usage:
        # Get users who read a message
        readers = MessageReadReceipt.objects.filter(
            message=message
        ).select_related("user")
    """

    # TODO: Implement model fields
    # message = models.ForeignKey(
    #     Message,
    #     on_delete=models.CASCADE,
    #     related_name="read_receipts",
    # )
    # user = models.ForeignKey(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.CASCADE,
    #     related_name="message_read_receipts",
    # )
    # read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Message Read Receipt"
        verbose_name_plural = "Message Read Receipts"
        # unique_together = [["message", "user"]]

    def __str__(self) -> str:
        # TODO: Implement
        # return f"{self.user.email} read message {self.message_id}"
        return "MessageReadReceipt"
