"""
Chat system models.

This module defines the data models for the chat system supporting:
- Direct (1:1) conversations between exactly two users
- Group conversations with role-based permissions

Models:
    Conversation: Container for messages between participants
    DirectConversationPair: Helper for enforcing uniqueness of direct conversations
    Participant: User participation in a conversation with role and tracking
    Message: Individual message within a conversation

Design Decisions:
    - Direct conversations are immutable once created (no adding/removing participants)
    - Group conversations use a three-tier role hierarchy: owner > admin > member
    - Participant records are immutable; leaving creates a new record on rejoin
    - Messages support single-level threading (replies to replies reference root)
    - Soft delete preserves audit trail while hiding content in API responses
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.models import BaseModel
from core.model_mixins import SoftDeleteMixin

if TYPE_CHECKING:
    from authentication.models import User


class ConversationType(models.TextChoices):
    """
    Type of conversation.

    DIRECT: Exactly two participants, immutable membership, no roles
    GROUP: Two or more participants, mutable membership, role-based permissions
    """

    DIRECT = "direct", "Direct Message"
    GROUP = "group", "Group"


class ParticipantRole(models.TextChoices):
    """
    Role within a group conversation.

    Hierarchy: OWNER > ADMIN > MEMBER

    OWNER: Full control (remove admins, demote, transfer ownership, delete conversation)
    ADMIN: Can add participants, remove members, change title
    MEMBER: Can send messages, delete own messages, leave

    Note: Direct conversations do not use roles (role is NULL for direct participants)
    """

    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


class MessageType(models.TextChoices):
    """
    Type of message content.

    TEXT: User-authored text message
    SYSTEM: Auto-generated event message (e.g., "User joined", "Title changed")
    """

    TEXT = "text", "Text"
    SYSTEM = "system", "System"


class SystemMessageEvent:
    """
    System message event types.

    System messages store structured event data as JSON in the content field.
    Format: {"event": "<event_type>", "data": {...event-specific data...}}

    Events:
        GROUP_CREATED: Group was created
            data: {"title": str}

        PARTICIPANT_ADDED: User was added to group
            data: {"user_id": str, "added_by_id": str}

        PARTICIPANT_REMOVED: User left or was removed from group
            data: {"user_id": str, "removed_by_id": str|None, "reason": "left"|"removed"}

        ROLE_CHANGED: User's role was changed
            data: {"user_id": str, "old_role": str, "new_role": str, "changed_by_id": str}

        OWNERSHIP_TRANSFERRED: Group ownership was transferred
            data: {"from_user_id": str, "to_user_id": str, "reason": "manual"|"departure"}

        TITLE_CHANGED: Group title was changed
            data: {"old_title": str, "new_title": str, "changed_by_id": str}
    """

    GROUP_CREATED = "group_created"
    PARTICIPANT_ADDED = "participant_added"
    PARTICIPANT_REMOVED = "participant_removed"
    ROLE_CHANGED = "role_changed"
    OWNERSHIP_TRANSFERRED = "ownership_transferred"
    TITLE_CHANGED = "title_changed"


class Conversation(SoftDeleteMixin, BaseModel):
    """
    A conversation between two or more users.

    Conversation Types:
        DIRECT: Exactly 2 participants, immutable membership, no title, no roles.
                Unique per user pair (enforced via DirectConversationPair).
                Soft deleted only when both users leave.

        GROUP: 2+ participants with role-based permissions.
               Creator automatically becomes owner.
               Soft deleted when no participants remain or owner deletes.

    Soft Delete Behavior:
        - is_deleted=True: Conversation is archived but data preserved
        - deleted_at: Timestamp when soft deleted
        - Participants can still access historical messages

    Fields:
        conversation_type: Type of conversation (direct or group)
        title: Group title (empty string for direct conversations)
        created_by: User who created the conversation (nullable for direct)
        participant_count: Cached count of active participants
        last_message_at: Timestamp of most recent message (for sorting)

    Relationships:
        participants: All Participant records for this conversation
        messages: All Message records for this conversation
        direct_pair: DirectConversationPair if type is DIRECT
    """

    conversation_type = models.CharField(
        max_length=10,
        choices=ConversationType.choices,
        default=ConversationType.GROUP,
        db_index=True,
        help_text="Type of conversation (direct or group)",
    )

    title = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Title for group conversations (empty for direct)",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_conversations",
        help_text="User who created this conversation (null for system-created)",
    )

    participant_count = models.PositiveIntegerField(
        default=0,
        help_text="Current number of active participants (cached for performance)",
    )

    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Timestamp of most recent message (for sorting conversation lists)",
    )

    class Meta:
        db_table = "chat_conversation"
        ordering = ["-last_message_at", "-created_at"]
        indexes = [
            # Filter by type and active status
            models.Index(
                fields=["conversation_type", "is_deleted"],
                name="chat_conv_type_deleted_idx",
            ),
            # Sort by last activity (active conversations only)
            models.Index(
                fields=["-last_message_at"],
                name="chat_conv_last_msg_idx",
                condition=Q(is_deleted=False),
            ),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        if self.conversation_type == ConversationType.DIRECT:
            return f"Direct({self.pk})"
        if self.title:
            return f"Group: {self.title}"
        return f"Group({self.pk})"

    @property
    def is_direct(self) -> bool:
        """Check if this is a direct (1:1) conversation."""
        return self.conversation_type == ConversationType.DIRECT

    @property
    def is_group(self) -> bool:
        """Check if this is a group conversation."""
        return self.conversation_type == ConversationType.GROUP

    def get_active_participants(self):
        """
        Get queryset of active participants.

        Returns:
            QuerySet of Participant objects where left_at is NULL
        """
        return self.participants.filter(left_at__isnull=True)

    def get_active_participant_for_user(self, user: User) -> Participant | None:
        """
        Get active participant record for a specific user.

        Args:
            user: User to find participant for

        Returns:
            Participant if user is active in conversation, None otherwise
        """
        return self.participants.filter(user=user, left_at__isnull=True).first()


class DirectConversationPair(models.Model):
    """
    Enforces uniqueness of direct conversations between two users.

    This helper table stores user pairs in canonical order (lower user_id first)
    to prevent duplicate direct conversations between the same two users.

    The uniqueness constraint ensures that regardless of who initiates the
    conversation, there can only be one direct conversation between any pair.

    Fields:
        conversation: The direct conversation (OneToOne, serves as PK)
        user_lower: User with lower ID
        user_higher: User with higher ID

    Constraints:
        - UniqueConstraint(user_lower, user_higher): One conversation per pair
        - CheckConstraint(user_lower_id < user_higher_id): Enforce canonical order

    Usage:
        To find or create a direct conversation between two users:
        1. Determine lower and higher user IDs
        2. Look up DirectConversationPair by (user_lower, user_higher)
        3. If found, return existing conversation
        4. If not found, create new Conversation and DirectConversationPair
    """

    conversation = models.OneToOneField(
        Conversation,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="direct_pair",
        help_text="The direct conversation this pair represents",
    )

    user_lower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",  # No reverse accessor needed
        help_text="User with lower ID in this conversation pair",
    )

    user_higher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",  # No reverse accessor needed
        help_text="User with higher ID in this conversation pair",
    )

    class Meta:
        db_table = "chat_direct_conversation_pair"
        constraints = [
            # Ensure only one direct conversation exists per user pair
            models.UniqueConstraint(
                fields=["user_lower", "user_higher"],
                name="unique_direct_conversation_pair",
            ),
            # Enforce canonical ordering: lower ID first
            models.CheckConstraint(
                check=Q(user_lower_id__lt=F("user_higher_id")),
                name="user_lower_less_than_higher",
            ),
        ]
        indexes = [
            # Fast lookup by user pair
            models.Index(
                fields=["user_lower", "user_higher"],
                name="chat_direct_pair_users_idx",
            ),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        return f"DirectPair({self.user_lower_id}, {self.user_higher_id})"


class Participant(BaseModel):
    """
    Tracks user participation in conversations.

    Design Decision:
        Each join creates a NEW Participant record to preserve full membership
        history. When a user leaves and later rejoins, they get a new record.
        Previous memberships are preserved with their left_at timestamps.

    Role Assignment:
        - Direct conversations: role is NULL (both users are equals)
        - Group conversations: role is required (owner/admin/member)
        - Creator of group automatically becomes OWNER
        - New participants join as MEMBER by default

    Membership Lifecycle:
        1. User joins: Participant created with left_at=NULL
        2. User leaves voluntarily: left_at set, left_voluntarily=True
        3. User removed: left_at set, left_voluntarily=False, removed_by set
        4. User rejoins: NEW Participant record created

    Fields:
        conversation: Conversation this participation belongs to
        user: User participating in the conversation
        role: Role in group conversation (NULL for direct)
        joined_at: When the user joined
        left_at: When the user left (NULL if still active)
        left_voluntarily: True if user left, False if removed
        removed_by: User who removed this participant (if applicable)
        last_read_at: Last time user read messages (for unread counts)

    Constraints:
        - UniqueConstraint(conversation, user) WHERE left_at IS NULL:
          Only one active participation per user per conversation
    """

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="participants",
        help_text="Conversation this participation belongs to",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_participations",
        help_text="User participating in the conversation",
    )

    role = models.CharField(
        max_length=10,
        choices=ParticipantRole.choices,
        null=True,
        blank=True,
        db_index=True,
        help_text="Role in group conversation (null for direct conversations)",
    )

    joined_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the user joined this conversation",
    )

    left_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the user left (null if still active)",
    )

    left_voluntarily = models.BooleanField(
        null=True,
        blank=True,
        help_text="True if user left voluntarily, False if removed by someone",
    )

    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="removed_participants",
        help_text="User who removed this participant (if removed by someone)",
    )

    last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time user marked conversation as read (for unread counts)",
    )

    class Meta:
        db_table = "chat_participant"
        ordering = ["joined_at"]
        indexes = [
            # Active participants in a conversation
            models.Index(
                fields=["conversation", "left_at"],
                name="chat_part_conv_active_idx",
            ),
            # User's active conversations
            models.Index(
                fields=["user", "left_at", "-joined_at"],
                name="chat_part_user_active_idx",
            ),
            # Role-based lookups (for ownership transfer)
            models.Index(
                fields=["conversation", "role", "joined_at"],
                name="chat_part_conv_role_idx",
                condition=Q(left_at__isnull=True),
            ),
        ]
        constraints = [
            # Only one active participation per user per conversation
            models.UniqueConstraint(
                fields=["conversation", "user"],
                condition=Q(left_at__isnull=True),
                name="unique_active_participation",
            ),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        status = "active" if self.is_active else "left"
        role_str = f" ({self.role})" if self.role else ""
        return f"Participant: {self.user_id} in {self.conversation_id}{role_str} [{status}]"

    @property
    def is_active(self) -> bool:
        """Check if this participation is currently active."""
        return self.left_at is None

    @property
    def is_owner(self) -> bool:
        """Check if participant has OWNER role."""
        return self.role == ParticipantRole.OWNER

    @property
    def is_admin(self) -> bool:
        """Check if participant has ADMIN role."""
        return self.role == ParticipantRole.ADMIN

    @property
    def is_member(self) -> bool:
        """Check if participant has MEMBER role."""
        return self.role == ParticipantRole.MEMBER

    @property
    def is_admin_or_owner(self) -> bool:
        """Check if participant has ADMIN or OWNER role."""
        return self.role in (ParticipantRole.OWNER, ParticipantRole.ADMIN)


class Message(SoftDeleteMixin, BaseModel):
    """
    A message within a conversation.

    Message Types:
        TEXT: User-authored message with text content
        SYSTEM: Auto-generated event message (sender is NULL)

    Soft Delete Behavior:
        When is_deleted=True:
        - Content is preserved in database for audit
        - API returns sender info but replaces content with "[Message deleted]"
        - Message still counts toward unread count

    Threading:
        Single-level threading is supported via parent_message.
        If a user replies to a reply, the service layer automatically
        normalizes it to reference the root message instead.

        Example:
            Root message (id=1, parent_message=NULL)
            ├── Reply (id=2, parent_message=1)
            └── Reply to reply (id=3, parent_message=1, NOT 2)

    Fields:
        conversation: Conversation this message belongs to
        sender: User who sent the message (NULL for system messages)
        message_type: Type of message (text or system)
        content: Message text or system event JSON
        parent_message: Parent message for threading (NULL if root)
        reply_count: Cached count of replies (for root messages)

    System Message Content Format:
        {"event": "<event_type>", "data": {...}}
        See SystemMessageEvent class for event types.
    """

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
        help_text="Conversation this message belongs to",
    )

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
        help_text="User who sent this message (null for system messages)",
    )

    message_type = models.CharField(
        max_length=10,
        choices=MessageType.choices,
        default=MessageType.TEXT,
        db_index=True,
        help_text="Type of message (text or system)",
    )

    content = models.TextField(
        help_text="Message content (text for user messages, JSON for system messages)",
    )

    parent_message = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
        help_text="Parent message for threading (null if root message)",
    )

    reply_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of replies to this message (cached for performance)",
    )

    class Meta:
        db_table = "chat_message"
        ordering = ["created_at", "id"]
        indexes = [
            # Messages in a conversation (cursor pagination)
            models.Index(
                fields=["conversation", "created_at", "id"],
                name="chat_msg_conv_cursor_idx",
            ),
            # Replies to a message
            models.Index(
                fields=["parent_message", "created_at"],
                name="chat_msg_parent_idx",
                condition=Q(parent_message__isnull=False),
            ),
            # User's messages
            models.Index(
                fields=["sender", "-created_at"],
                name="chat_msg_sender_idx",
            ),
            # System messages in conversation
            models.Index(
                fields=["conversation", "message_type"],
                name="chat_msg_conv_type_idx",
            ),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        sender_str = f"User {self.sender_id}" if self.sender_id else "System"
        content_preview = (
            self.content[:50] + "..." if len(self.content) > 50 else self.content
        )
        deleted_str = " [deleted]" if self.is_deleted else ""
        return f"{sender_str}: {content_preview}{deleted_str}"

    @property
    def is_system_message(self) -> bool:
        """Check if this is a system-generated message."""
        return self.message_type == MessageType.SYSTEM

    @property
    def is_text_message(self) -> bool:
        """Check if this is a user-authored text message."""
        return self.message_type == MessageType.TEXT

    @property
    def is_reply(self) -> bool:
        """Check if this message is a reply to another message."""
        return self.parent_message_id is not None

    def get_system_event_data(self) -> dict | None:
        """
        Parse system message content as JSON.

        Returns:
            Dict with 'event' and 'data' keys if system message, None otherwise
        """
        if not self.is_system_message:
            return None
        try:
            return json.loads(self.content)
        except (json.JSONDecodeError, TypeError):
            return None

    def get_display_content(self) -> str:
        """
        Get content suitable for display.

        Returns:
            - "[Message deleted]" if soft deleted
            - Original content otherwise
        """
        if self.is_deleted:
            return "[Message deleted]"
        return self.content
