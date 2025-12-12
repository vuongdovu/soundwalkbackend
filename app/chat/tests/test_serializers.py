"""
Comprehensive tests for chat serializers.

This module tests all chat serializers following TDD principles:
- ConversationCreateSerializer: Conversation creation validation
- ConversationUpdateSerializer: Title update validation
- ParticipantCreateSerializer: Participant addition validation
- ParticipantUpdateSerializer: Role change validation
- MessageCreateSerializer: Message sending validation
- MessageSerializer: Message output serialization
- ConversationListSerializer: List view with computed fields

Test Organization:
    - Each serializer has its own test class
    - Each test validates ONE specific behavior
    - Tests focus on validation and output structure

Testing Philosophy:
    - Test validation error cases with specific messages
    - Test computed fields with realistic data
    - Test soft-deleted message content replacement
"""

import json

from rest_framework.test import APIRequestFactory
from rest_framework.request import Request

from authentication.tests.factories import UserFactory
from chat.models import (
    ConversationType,
    ParticipantRole,
)
from chat.serializers import (
    ConversationCreateSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    ConversationUpdateSerializer,
    MessageCreateSerializer,
    MessagePreviewSerializer,
    MessageSerializer,
    ParticipantCreateSerializer,
    ParticipantSerializer,
    ParticipantUpdateSerializer,
    format_system_message,
)


# =============================================================================
# Helper Functions
# =============================================================================


def make_request(user=None):
    """Create a DRF Request object with optional user."""
    factory = APIRequestFactory()
    request = factory.get("/")
    drf_request = Request(request)
    if user:
        # Force authentication
        drf_request._user = user
        drf_request._request.user = user
    return drf_request


# =============================================================================
# TestFormatSystemMessage
# =============================================================================


class TestFormatSystemMessage:
    """
    Tests for format_system_message helper function.

    Verifies:
    - Correct formatting for each event type
    - Graceful handling of invalid JSON
    """

    def test_formats_group_created(self):
        """GROUP_CREATED event formats correctly."""
        content = json.dumps(
            {"event": "group_created", "data": {"title": "Test Group"}}
        )

        result = format_system_message(content)

        assert "Test Group" in result
        assert "created" in result

    def test_formats_participant_added(self):
        """PARTICIPANT_ADDED event formats correctly."""
        content = json.dumps(
            {"event": "participant_added", "data": {"user_id": 1, "added_by_id": 2}}
        )

        result = format_system_message(content)

        assert "added" in result

    def test_formats_participant_left(self):
        """PARTICIPANT_REMOVED with reason=left formats correctly."""
        content = json.dumps(
            {"event": "participant_removed", "data": {"user_id": 1, "reason": "left"}}
        )

        result = format_system_message(content)

        assert "left" in result

    def test_formats_participant_removed(self):
        """PARTICIPANT_REMOVED with reason=removed formats correctly."""
        content = json.dumps(
            {
                "event": "participant_removed",
                "data": {"user_id": 1, "reason": "removed"},
            }
        )

        result = format_system_message(content)

        assert "removed" in result

    def test_formats_role_changed(self):
        """ROLE_CHANGED event formats correctly."""
        content = json.dumps(
            {"event": "role_changed", "data": {"user_id": 1, "new_role": "admin"}}
        )

        result = format_system_message(content)

        assert "admin" in result

    def test_formats_ownership_transferred(self):
        """OWNERSHIP_TRANSFERRED event formats correctly."""
        content = json.dumps(
            {
                "event": "ownership_transferred",
                "data": {"from_user_id": 1, "to_user_id": 2},
            }
        )

        result = format_system_message(content)

        assert "transferred" in result

    def test_formats_title_changed(self):
        """TITLE_CHANGED event formats correctly."""
        content = json.dumps(
            {"event": "title_changed", "data": {"new_title": "New Title"}}
        )

        result = format_system_message(content)

        assert "New Title" in result

    def test_handles_invalid_json(self):
        """Invalid JSON returns generic message."""
        result = format_system_message("not json")

        assert result == "System message"

    def test_handles_unknown_event(self):
        """Unknown event type returns generic message."""
        content = json.dumps({"event": "unknown_event", "data": {}})

        result = format_system_message(content)

        assert result == "System message"


# =============================================================================
# TestConversationCreateSerializer
# =============================================================================


class TestConversationCreateSerializer:
    """
    Tests for ConversationCreateSerializer.

    Verifies:
    - Direct conversation validation (exactly one participant)
    - Group conversation validation (title required)
    - Participant ID validation
    """

    def test_valid_direct_conversation(self, db, other_user, owner_user):
        """Valid direct conversation data passes validation."""
        request = make_request(owner_user)
        serializer = ConversationCreateSerializer(
            data={
                "conversation_type": "direct",
                "participant_ids": [other_user.id],
            },
            context={"request": request},
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["conversation_type"] == ConversationType.DIRECT

    def test_valid_group_conversation(self, db, other_user, owner_user):
        """Valid group conversation data passes validation."""
        request = make_request(owner_user)
        serializer = ConversationCreateSerializer(
            data={
                "conversation_type": "group",
                "title": "Test Group",
                "participant_ids": [other_user.id],
            },
            context={"request": request},
        )

        assert serializer.is_valid(), serializer.errors

    def test_direct_requires_exactly_one_participant(self, db, owner_user, other_user):
        """Direct conversation must have exactly one other participant."""
        request = make_request(owner_user)
        user2 = UserFactory()

        serializer = ConversationCreateSerializer(
            data={
                "conversation_type": "direct",
                "participant_ids": [other_user.id, user2.id],  # Two participants
            },
            context={"request": request},
        )

        assert not serializer.is_valid()
        assert "participant_ids" in serializer.errors

    def test_direct_cannot_have_title(self, db, owner_user, other_user):
        """Direct conversation cannot have a title."""
        request = make_request(owner_user)

        serializer = ConversationCreateSerializer(
            data={
                "conversation_type": "direct",
                "participant_ids": [other_user.id],
                "title": "Should Not Be Here",
            },
            context={"request": request},
        )

        assert not serializer.is_valid()
        assert "title" in serializer.errors

    def test_group_requires_title(self, db, owner_user, other_user):
        """Group conversation must have a title."""
        request = make_request(owner_user)

        serializer = ConversationCreateSerializer(
            data={
                "conversation_type": "group",
                "participant_ids": [other_user.id],
                # No title
            },
            context={"request": request},
        )

        assert not serializer.is_valid()
        assert "title" in serializer.errors

    def test_rejects_self_in_participants(self, db, owner_user):
        """Cannot include yourself in participant list."""
        request = make_request(owner_user)

        serializer = ConversationCreateSerializer(
            data={
                "conversation_type": "direct",
                "participant_ids": [owner_user.id],  # Self
            },
            context={"request": request},
        )

        assert not serializer.is_valid()
        assert "participant_ids" in serializer.errors

    def test_rejects_nonexistent_user_id(self, db, owner_user):
        """Rejects participant IDs that don't exist."""
        request = make_request(owner_user)

        serializer = ConversationCreateSerializer(
            data={
                "conversation_type": "direct",
                "participant_ids": [99999],  # Nonexistent
            },
            context={"request": request},
        )

        assert not serializer.is_valid()
        assert "participant_ids" in serializer.errors

    def test_rejects_inactive_user(self, db, owner_user):
        """Rejects inactive user IDs."""
        request = make_request(owner_user)
        inactive_user = UserFactory(is_active=False)

        serializer = ConversationCreateSerializer(
            data={
                "conversation_type": "direct",
                "participant_ids": [inactive_user.id],
            },
            context={"request": request},
        )

        assert not serializer.is_valid()
        assert "participant_ids" in serializer.errors


# =============================================================================
# TestConversationUpdateSerializer
# =============================================================================


class TestConversationUpdateSerializer:
    """
    Tests for ConversationUpdateSerializer.

    Verifies:
    - Title validation (required, non-empty)
    """

    def test_valid_title_update(self):
        """Valid title passes validation."""
        serializer = ConversationUpdateSerializer(data={"title": "New Title"})

        assert serializer.is_valid()
        assert serializer.validated_data["title"] == "New Title"

    def test_rejects_empty_title(self):
        """Empty title fails validation."""
        serializer = ConversationUpdateSerializer(data={"title": ""})

        assert not serializer.is_valid()
        assert "title" in serializer.errors

    def test_rejects_whitespace_title(self):
        """Whitespace-only title fails validation."""
        serializer = ConversationUpdateSerializer(data={"title": "   "})

        assert not serializer.is_valid()
        assert "title" in serializer.errors

    def test_strips_whitespace_from_title(self):
        """Title is stripped of leading/trailing whitespace."""
        serializer = ConversationUpdateSerializer(data={"title": "  Trimmed Title  "})

        assert serializer.is_valid()
        assert serializer.validated_data["title"] == "Trimmed Title"


# =============================================================================
# TestParticipantCreateSerializer
# =============================================================================


class TestParticipantCreateSerializer:
    """
    Tests for ParticipantCreateSerializer.

    Verifies:
    - User ID validation
    - Role validation (no OWNER role)
    """

    def test_valid_member_creation(self, db, non_participant_user):
        """Valid member addition passes validation."""
        serializer = ParticipantCreateSerializer(
            data={
                "user_id": non_participant_user.id,
                "role": "member",
            }
        )

        assert serializer.is_valid()

    def test_valid_admin_creation(self, db, non_participant_user):
        """Valid admin addition passes validation."""
        serializer = ParticipantCreateSerializer(
            data={
                "user_id": non_participant_user.id,
                "role": "admin",
            }
        )

        assert serializer.is_valid()

    def test_default_role_is_member(self, db, non_participant_user):
        """Role defaults to member if not specified."""
        serializer = ParticipantCreateSerializer(
            data={
                "user_id": non_participant_user.id,
            }
        )

        assert serializer.is_valid()
        assert serializer.validated_data["role"] == ParticipantRole.MEMBER

    def test_rejects_owner_role(self, db, non_participant_user):
        """Cannot create participant with OWNER role."""
        serializer = ParticipantCreateSerializer(
            data={
                "user_id": non_participant_user.id,
                "role": "owner",
            }
        )

        assert not serializer.is_valid()
        assert "role" in serializer.errors

    def test_rejects_nonexistent_user(self, db):
        """Rejects nonexistent user IDs."""
        serializer = ParticipantCreateSerializer(
            data={
                "user_id": 99999,
                "role": "member",
            }
        )

        assert not serializer.is_valid()
        assert "user_id" in serializer.errors

    def test_rejects_inactive_user(self, db):
        """Rejects inactive user IDs."""
        inactive_user = UserFactory(is_active=False)

        serializer = ParticipantCreateSerializer(
            data={
                "user_id": inactive_user.id,
                "role": "member",
            }
        )

        assert not serializer.is_valid()
        assert "user_id" in serializer.errors


# =============================================================================
# TestParticipantUpdateSerializer
# =============================================================================


class TestParticipantUpdateSerializer:
    """
    Tests for ParticipantUpdateSerializer.

    Verifies:
    - Role validation (admin or member only)
    """

    def test_valid_admin_role(self):
        """Admin role passes validation."""
        serializer = ParticipantUpdateSerializer(data={"role": "admin"})

        assert serializer.is_valid()

    def test_valid_member_role(self):
        """Member role passes validation."""
        serializer = ParticipantUpdateSerializer(data={"role": "member"})

        assert serializer.is_valid()

    def test_rejects_owner_role(self):
        """Owner role is rejected (use transfer ownership)."""
        serializer = ParticipantUpdateSerializer(data={"role": "owner"})

        assert not serializer.is_valid()
        assert "role" in serializer.errors


# =============================================================================
# TestMessageCreateSerializer
# =============================================================================


class TestMessageCreateSerializer:
    """
    Tests for MessageCreateSerializer.

    Verifies:
    - Content validation
    - Optional parent_id
    """

    def test_valid_message(self):
        """Valid message passes validation."""
        serializer = MessageCreateSerializer(data={"content": "Hello, world!"})

        assert serializer.is_valid()

    def test_valid_reply(self, db, text_message):
        """Valid reply with parent_id passes validation."""
        serializer = MessageCreateSerializer(
            data={
                "content": "This is a reply",
                "parent_id": text_message.id,
            }
        )

        assert serializer.is_valid()

    def test_rejects_empty_content(self):
        """Empty content fails validation."""
        serializer = MessageCreateSerializer(data={"content": ""})

        assert not serializer.is_valid()
        assert "content" in serializer.errors

    def test_rejects_too_long_content(self):
        """Content exceeding max length fails validation."""
        serializer = MessageCreateSerializer(
            data={"content": "x" * 10001}  # Over 10,000 char limit
        )

        assert not serializer.is_valid()
        assert "content" in serializer.errors

    def test_parent_id_optional(self):
        """parent_id is optional."""
        serializer = MessageCreateSerializer(data={"content": "No reply"})

        assert serializer.is_valid()
        assert serializer.validated_data.get("parent_id") is None


# =============================================================================
# TestMessageSerializer
# =============================================================================


class TestMessageSerializer:
    """
    Tests for MessageSerializer (output serialization).

    Verifies:
    - Deleted message content replacement
    - System message formatting
    - Field structure
    """

    def test_includes_expected_fields(self, db, text_message):
        """Output includes all expected fields."""
        serializer = MessageSerializer(text_message)
        data = serializer.data

        assert "id" in data
        assert "conversation_id" in data
        assert "sender" in data
        assert "content" in data
        assert "message_type" in data
        assert "is_deleted" in data
        assert "parent_id" in data
        assert "reply_count" in data
        assert "created_at" in data

    def test_deleted_message_shows_placeholder(self, db, deleted_message):
        """Deleted message content is replaced with placeholder."""
        serializer = MessageSerializer(deleted_message)
        data = serializer.data

        assert data["content"] == "[Message deleted]"
        assert data["is_deleted"] is True

    def test_system_message_shows_formatted_content(self, db, system_message):
        """System message content is formatted for display."""
        serializer = MessageSerializer(system_message)
        data = serializer.data

        # Should not show raw JSON
        assert "{" not in data["content"]
        assert data["sender"] is None

    def test_includes_sender_info(self, db, text_message):
        """Sender object is included with user details."""
        serializer = MessageSerializer(text_message)
        data = serializer.data

        assert data["sender"] is not None
        assert "id" in data["sender"]
        assert "email" in data["sender"]


# =============================================================================
# TestMessagePreviewSerializer
# =============================================================================


class TestMessagePreviewSerializer:
    """
    Tests for MessagePreviewSerializer (minimal output).

    Used for last message preview in conversation lists.
    """

    def test_includes_minimal_fields(self, db, text_message):
        """Output includes only minimal fields needed for preview."""
        serializer = MessagePreviewSerializer(text_message)
        data = serializer.data

        assert "id" in data
        assert "sender_name" in data
        assert "content" in data
        assert "message_type" in data
        assert "created_at" in data

    def test_sender_name_from_user(self, db, text_message):
        """sender_name comes from user's display name."""
        serializer = MessagePreviewSerializer(text_message)
        data = serializer.data

        # Should be full name or email
        assert data["sender_name"] is not None

    def test_sender_name_none_for_system(self, db, system_message):
        """System messages have null sender_name."""
        serializer = MessagePreviewSerializer(system_message)
        data = serializer.data

        assert data["sender_name"] is None

    def test_deleted_content_placeholder(self, db, deleted_message):
        """Deleted messages show placeholder content."""
        serializer = MessagePreviewSerializer(deleted_message)
        data = serializer.data

        assert data["content"] == "[Message deleted]"


# =============================================================================
# TestParticipantSerializer
# =============================================================================


class TestParticipantSerializer:
    """
    Tests for ParticipantSerializer (output serialization).
    """

    def test_includes_expected_fields(self, db, owner_participant):
        """Output includes all expected fields."""
        serializer = ParticipantSerializer(owner_participant)
        data = serializer.data

        assert "id" in data
        assert "user" in data
        assert "role" in data
        assert "joined_at" in data
        assert "is_active" in data

    def test_includes_user_details(self, db, owner_participant):
        """User object includes details."""
        serializer = ParticipantSerializer(owner_participant)
        data = serializer.data

        assert data["user"] is not None
        assert "id" in data["user"]
        assert "email" in data["user"]


# =============================================================================
# TestConversationListSerializer
# =============================================================================


class TestConversationListSerializer:
    """
    Tests for ConversationListSerializer.

    Verifies computed fields:
    - unread_count
    - last_message
    - display_name
    - other_participants
    """

    def test_includes_expected_fields(self, db, group_conversation, owner_user):
        """Output includes all expected fields."""
        request = make_request(owner_user)
        serializer = ConversationListSerializer(
            group_conversation,
            context={"request": request},
        )
        data = serializer.data

        assert "id" in data
        assert "conversation_type" in data
        assert "title" in data
        assert "display_name" in data
        assert "participant_count" in data
        assert "unread_count" in data
        assert "last_message" in data
        assert "other_participants" in data

    def test_display_name_uses_title_for_group(
        self, db, group_conversation, owner_user
    ):
        """Group conversation display_name is the title."""
        request = make_request(owner_user)
        serializer = ConversationListSerializer(
            group_conversation,
            context={"request": request},
        )
        data = serializer.data

        assert data["display_name"] == group_conversation.title

    def test_display_name_uses_other_user_for_direct(
        self, db, direct_conversation, owner_user
    ):
        """Direct conversation display_name is the other user's name."""
        request = make_request(owner_user)
        serializer = ConversationListSerializer(
            direct_conversation,
            context={"request": request},
        )
        data = serializer.data

        # Should be the other user's name or email
        assert data["display_name"] != ""
        assert data["display_name"] != direct_conversation.title

    def test_unread_count_for_participant(
        self, db, conversation_with_messages, owner_user
    ):
        """unread_count reflects messages since last_read_at."""
        request = make_request(owner_user)
        conv = conversation_with_messages["conversation"]

        serializer = ConversationListSerializer(
            conv,
            context={"request": request},
        )
        data = serializer.data

        # Should have unread count (messages from others)
        assert "unread_count" in data
        assert isinstance(data["unread_count"], int)

    def test_last_message_preview(
        self, db, text_message, group_conversation, owner_user
    ):
        """last_message contains message preview."""
        # Update last_message_at
        group_conversation.last_message_at = text_message.created_at
        group_conversation.save()

        request = make_request(owner_user)
        serializer = ConversationListSerializer(
            group_conversation,
            context={"request": request},
        )
        data = serializer.data

        assert data["last_message"] is not None
        assert "content" in data["last_message"]

    def test_other_participants_excludes_self(
        self, db, group_conversation_with_members, owner_user
    ):
        """other_participants excludes the current user."""
        request = make_request(owner_user)
        serializer = ConversationListSerializer(
            group_conversation_with_members,
            context={"request": request},
        )
        data = serializer.data

        other_ids = [p["id"] for p in data["other_participants"]]
        assert owner_user.id not in other_ids


# =============================================================================
# TestConversationDetailSerializer
# =============================================================================


class TestConversationDetailSerializer:
    """
    Tests for ConversationDetailSerializer.

    Extends ConversationListSerializer with:
    - participants (full list)
    - current_user_role
    """

    def test_includes_participants_list(
        self, db, group_conversation_with_members, owner_user
    ):
        """Output includes full participants list."""
        request = make_request(owner_user)
        serializer = ConversationDetailSerializer(
            group_conversation_with_members,
            context={"request": request},
        )
        data = serializer.data

        assert "participants" in data
        assert len(data["participants"]) == 3

    def test_includes_current_user_role(
        self, db, group_conversation_with_members, owner_user
    ):
        """Output includes current user's role."""
        request = make_request(owner_user)
        serializer = ConversationDetailSerializer(
            group_conversation_with_members,
            context={"request": request},
        )
        data = serializer.data

        # Role is serialized as string value
        assert data["current_user_role"] == "owner"

    def test_current_user_role_none_for_non_participant(
        self, db, group_conversation, non_participant_user
    ):
        """Non-participant gets null for current_user_role."""
        request = make_request(non_participant_user)
        serializer = ConversationDetailSerializer(
            group_conversation,
            context={"request": request},
        )
        data = serializer.data

        assert data["current_user_role"] is None
