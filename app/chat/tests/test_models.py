"""
Comprehensive tests for chat model constraints and computed properties.

This module tests the chat models following TDD principles:
- Conversation: Type constraints, computed properties, soft delete
- DirectConversationPair: Uniqueness constraint, canonical ordering
- Participant: Active participation constraint, role properties
- Message: Threading, soft delete, system message parsing

Test Organization:
    - Each model has its own test class
    - Each test validates ONE specific behavior
    - Tests use descriptive names following: test_<scenario>_<expected_outcome>

Testing Philosophy:
    Tests focus on observable behavior, not implementation details:
    - Database constraints enforcement
    - Computed property correctness
    - Model method behavior
    - Query helper correctness
"""

import json

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from authentication.tests.factories import UserFactory
from chat.models import (
    Conversation,
    ConversationType,
    DirectConversationPair,
    Message,
    MessageType,
    Participant,
    ParticipantRole,
    SystemMessageEvent,
)


# =============================================================================
# TestConversation
# =============================================================================


class TestConversation:
    """
    Tests for Conversation model.

    Verifies:
    - Type-specific behavior (direct vs group)
    - Computed properties (is_direct, is_group)
    - Active participant queries
    - Soft delete behavior
    """

    # -------------------------------------------------------------------------
    # Type Properties
    # -------------------------------------------------------------------------

    def test_is_direct_returns_true_for_direct_conversation(
        self, db, direct_conversation
    ):
        """
        is_direct property returns True for direct conversations.

        Why it matters: Frontend and service layer use this to determine
        which operations are allowed.
        """
        assert direct_conversation.is_direct is True
        assert direct_conversation.is_group is False

    def test_is_group_returns_true_for_group_conversation(self, db, group_conversation):
        """
        is_group property returns True for group conversations.

        Why it matters: Group conversations have different behavior
        (roles, title updates, etc.) than direct conversations.
        """
        assert group_conversation.is_group is True
        assert group_conversation.is_direct is False

    # -------------------------------------------------------------------------
    # Active Participants
    # -------------------------------------------------------------------------

    def test_get_active_participants_excludes_left_users(
        self, db, group_conversation, owner_user
    ):
        """
        get_active_participants() excludes users who have left.

        Why it matters: Left participants should not be counted or displayed
        in the active participant list.
        """
        # Add a member who leaves
        left_user = UserFactory()
        Participant.objects.create(
            conversation=group_conversation,
            user=left_user,
            role=ParticipantRole.MEMBER,
            left_at=timezone.now(),
            left_voluntarily=True,
        )

        active = group_conversation.get_active_participants()

        assert active.count() == 1
        assert active.first().user == owner_user

    def test_get_active_participants_returns_all_active(
        self, db, group_conversation_with_members
    ):
        """
        get_active_participants() returns all users who haven't left.

        Why it matters: Conversation displays need accurate participant lists.
        """
        active = group_conversation_with_members.get_active_participants()

        assert active.count() == 3

    def test_get_active_participant_for_user_returns_participant(
        self, db, group_conversation, owner_user
    ):
        """
        get_active_participant_for_user() returns the participant for an active user.

        Why it matters: Permission checks and operations need to find
        the user's participant record efficiently.
        """
        participant = group_conversation.get_active_participant_for_user(owner_user)

        assert participant is not None
        assert participant.user == owner_user
        assert participant.role == ParticipantRole.OWNER

    def test_get_active_participant_for_user_returns_none_for_left_user(
        self, db, group_conversation
    ):
        """
        get_active_participant_for_user() returns None for users who left.

        Why it matters: Left users should not be found as active participants.
        """
        left_user = UserFactory()
        Participant.objects.create(
            conversation=group_conversation,
            user=left_user,
            role=ParticipantRole.MEMBER,
            left_at=timezone.now(),
            left_voluntarily=True,
        )

        participant = group_conversation.get_active_participant_for_user(left_user)

        assert participant is None

    def test_get_active_participant_for_user_returns_none_for_non_participant(
        self, db, group_conversation, non_participant_user
    ):
        """
        get_active_participant_for_user() returns None for non-participants.

        Why it matters: Users not in the conversation should not be found.
        """
        participant = group_conversation.get_active_participant_for_user(
            non_participant_user
        )

        assert participant is None

    # -------------------------------------------------------------------------
    # String Representation
    # -------------------------------------------------------------------------

    def test_str_direct_conversation(self, db, direct_conversation):
        """
        Direct conversation __str__ includes type indicator.

        Why it matters: Debugging and admin panel clarity.
        """
        result = str(direct_conversation)

        assert "Direct" in result

    def test_str_group_with_title(self, db, group_conversation):
        """
        Group conversation __str__ shows title when present.

        Why it matters: Easier identification in admin and logs.
        """
        result = str(group_conversation)

        assert group_conversation.title in result

    def test_str_group_without_title(self, db, owner_user):
        """
        Group conversation without title shows placeholder.

        Why it matters: Handles edge case of empty title gracefully.
        """
        conversation = Conversation.objects.create(
            conversation_type=ConversationType.GROUP,
            title="",
            created_by=owner_user,
        )

        result = str(conversation)

        assert "Group" in result


# =============================================================================
# TestDirectConversationPair
# =============================================================================


class TestDirectConversationPair:
    """
    Tests for DirectConversationPair model.

    Verifies:
    - Uniqueness constraint (one conversation per user pair)
    - Canonical ordering (lower ID first)
    - Database constraints are properly enforced
    """

    def test_unique_constraint_prevents_duplicate_pairs(self, db, direct_conversation):
        """
        Cannot create two DirectConversationPairs for the same user pair.

        Why it matters: This is the core uniqueness guarantee for direct
        conversations. Violations indicate a bug in the service layer.
        """
        pair = DirectConversationPair.objects.get(conversation=direct_conversation)

        # Try to create another conversation with same users
        new_conversation = Conversation.objects.create(
            conversation_type=ConversationType.DIRECT,
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                DirectConversationPair.objects.create(
                    conversation=new_conversation,
                    user_lower=pair.user_lower,
                    user_higher=pair.user_higher,
                )

    def test_check_constraint_enforces_canonical_order(self, db):
        """
        Cannot create DirectConversationPair with lower ID in user_higher field.

        Why it matters: The check constraint ensures the service layer
        canonicalizes the order correctly. Violations are caught at DB level.
        """
        user1 = UserFactory()
        user2 = UserFactory()

        # Ensure user1 has lower ID
        if user1.id > user2.id:
            user1, user2 = user2, user1

        conversation = Conversation.objects.create(
            conversation_type=ConversationType.DIRECT,
        )

        # Try to create with wrong order (higher ID in user_lower)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                DirectConversationPair.objects.create(
                    conversation=conversation,
                    user_lower=user2,  # Wrong: higher ID
                    user_higher=user1,  # Wrong: lower ID
                )

    def test_str_representation(self, db, direct_conversation):
        """
        __str__ shows both user IDs for debugging.

        Why it matters: Clear identification in admin and logs.
        """
        pair = DirectConversationPair.objects.get(conversation=direct_conversation)

        result = str(pair)

        assert str(pair.user_lower_id) in result
        assert str(pair.user_higher_id) in result


# =============================================================================
# TestParticipant
# =============================================================================


class TestParticipant:
    """
    Tests for Participant model.

    Verifies:
    - Active participation uniqueness constraint
    - Role-based computed properties
    - Leave behavior
    """

    # -------------------------------------------------------------------------
    # Uniqueness Constraint
    # -------------------------------------------------------------------------

    def test_unique_active_participation_allows_one_active_per_user(
        self, db, group_conversation, owner_user
    ):
        """
        Cannot have two active participations for same user in same conversation.

        Why it matters: Each user should have exactly one active record per
        conversation. This prevents duplicate membership.
        """
        other_user = UserFactory()

        # Add user as member
        Participant.objects.create(
            conversation=group_conversation,
            user=other_user,
            role=ParticipantRole.MEMBER,
        )

        # Try to add same user again
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Participant.objects.create(
                    conversation=group_conversation,
                    user=other_user,
                    role=ParticipantRole.MEMBER,
                )

    def test_unique_constraint_allows_rejoin_after_leaving(
        self, db, group_conversation
    ):
        """
        User can rejoin after leaving (creates new participant record).

        Why it matters: The constraint only applies to active participations.
        Left participations should not block rejoining.
        """
        user = UserFactory()

        # First membership (then left)
        old_participation = Participant.objects.create(
            conversation=group_conversation,
            user=user,
            role=ParticipantRole.MEMBER,
            left_at=timezone.now(),
            left_voluntarily=True,
        )

        # Rejoin (new record) - should not raise
        new_participation = Participant.objects.create(
            conversation=group_conversation,
            user=user,
            role=ParticipantRole.MEMBER,
        )

        assert old_participation.pk != new_participation.pk
        assert new_participation.left_at is None

    # -------------------------------------------------------------------------
    # Role Properties
    # -------------------------------------------------------------------------

    def test_is_active_true_when_not_left(self, db, group_conversation, owner_user):
        """
        is_active returns True when left_at is None.

        Why it matters: Used throughout the system to check membership status.
        """
        participant = Participant.objects.get(
            conversation=group_conversation,
            user=owner_user,
        )

        assert participant.is_active is True

    def test_is_active_false_when_left(self, db, group_conversation):
        """
        is_active returns False when left_at is set.

        Why it matters: Left participants should not be treated as active.
        """
        user = UserFactory()
        participant = Participant.objects.create(
            conversation=group_conversation,
            user=user,
            role=ParticipantRole.MEMBER,
            left_at=timezone.now(),
        )

        assert participant.is_active is False

    def test_is_owner_true_for_owner_role(self, db, group_conversation, owner_user):
        """
        is_owner returns True for OWNER role.

        Why it matters: Permission checks use this property.
        """
        participant = Participant.objects.get(
            conversation=group_conversation,
            user=owner_user,
        )

        assert participant.is_owner is True
        assert participant.is_admin is False
        assert participant.is_member is False

    def test_is_admin_true_for_admin_role(
        self, db, group_conversation_with_members, admin_user
    ):
        """
        is_admin returns True for ADMIN role.

        Why it matters: Permission checks use this property.
        """
        participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=admin_user,
        )

        assert participant.is_admin is True
        assert participant.is_owner is False
        assert participant.is_member is False

    def test_is_member_true_for_member_role(
        self, db, group_conversation_with_members, member_user
    ):
        """
        is_member returns True for MEMBER role.

        Why it matters: Permission checks use this property.
        """
        participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
        )

        assert participant.is_member is True
        assert participant.is_owner is False
        assert participant.is_admin is False

    def test_is_admin_or_owner_true_for_owner(self, db, group_conversation, owner_user):
        """
        is_admin_or_owner returns True for OWNER role.

        Why it matters: Combined check for admin-level operations.
        """
        participant = Participant.objects.get(
            conversation=group_conversation,
            user=owner_user,
        )

        assert participant.is_admin_or_owner is True

    def test_is_admin_or_owner_true_for_admin(
        self, db, group_conversation_with_members, admin_user
    ):
        """
        is_admin_or_owner returns True for ADMIN role.

        Why it matters: Combined check for admin-level operations.
        """
        participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=admin_user,
        )

        assert participant.is_admin_or_owner is True

    def test_is_admin_or_owner_false_for_member(
        self, db, group_conversation_with_members, member_user
    ):
        """
        is_admin_or_owner returns False for MEMBER role.

        Why it matters: Members should not pass admin-level checks.
        """
        participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
        )

        assert participant.is_admin_or_owner is False

    def test_direct_conversation_participant_has_no_role(
        self, db, direct_conversation, owner_user
    ):
        """
        Direct conversation participants have role=None.

        Why it matters: Direct conversations don't use role hierarchy.
        """
        participant = direct_conversation.participants.first()

        assert participant.role is None
        assert participant.is_owner is False
        assert participant.is_admin is False
        assert participant.is_member is False


# =============================================================================
# TestMessage
# =============================================================================


class TestMessage:
    """
    Tests for Message model.

    Verifies:
    - Message type properties (text vs system)
    - Threading (is_reply property)
    - Soft delete (display content)
    - System message parsing
    """

    # -------------------------------------------------------------------------
    # Type Properties
    # -------------------------------------------------------------------------

    def test_is_text_message_true_for_text_type(self, db, text_message):
        """
        is_text_message returns True for TEXT message type.

        Why it matters: Used to determine rendering and behavior.
        """
        assert text_message.is_text_message is True
        assert text_message.is_system_message is False

    def test_is_system_message_true_for_system_type(self, db, system_message):
        """
        is_system_message returns True for SYSTEM message type.

        Why it matters: System messages are rendered differently.
        """
        assert system_message.is_system_message is True
        assert system_message.is_text_message is False

    # -------------------------------------------------------------------------
    # Threading
    # -------------------------------------------------------------------------

    def test_is_reply_false_for_root_message(self, db, text_message):
        """
        is_reply returns False for messages without parent.

        Why it matters: Distinguishes root messages from replies.
        """
        assert text_message.is_reply is False

    def test_is_reply_true_for_reply_message(self, db, threading_scenario):
        """
        is_reply returns True for messages with parent.

        Why it matters: Reply messages are grouped under their parent.
        """
        reply = threading_scenario["reply"]

        assert reply.is_reply is True
        assert reply.parent_message == threading_scenario["root"]

    # -------------------------------------------------------------------------
    # Soft Delete
    # -------------------------------------------------------------------------

    def test_get_display_content_returns_content_when_not_deleted(
        self, db, text_message
    ):
        """
        get_display_content returns original content for non-deleted messages.

        Why it matters: Normal messages should show their content.
        """
        result = text_message.get_display_content()

        assert result == text_message.content

    def test_get_display_content_returns_placeholder_when_deleted(
        self, db, deleted_message
    ):
        """
        get_display_content returns placeholder for deleted messages.

        Why it matters: Deleted messages should hide their content but
        maintain thread structure.
        """
        result = deleted_message.get_display_content()

        assert result == "[Message deleted]"
        assert deleted_message.content != "[Message deleted]"  # Original preserved

    # -------------------------------------------------------------------------
    # System Message Parsing
    # -------------------------------------------------------------------------

    def test_get_system_event_data_returns_parsed_json(self, db, group_conversation):
        """
        get_system_event_data returns parsed JSON for system messages.

        Why it matters: System messages store structured event data.
        """
        content = json.dumps(
            {"event": SystemMessageEvent.GROUP_CREATED, "data": {"title": "Test Group"}}
        )
        message = Message.objects.create(
            conversation=group_conversation,
            sender=None,
            message_type=MessageType.SYSTEM,
            content=content,
        )

        result = message.get_system_event_data()

        assert result is not None
        assert result["event"] == SystemMessageEvent.GROUP_CREATED
        assert result["data"]["title"] == "Test Group"

    def test_get_system_event_data_returns_none_for_text_messages(
        self, db, text_message
    ):
        """
        get_system_event_data returns None for text messages.

        Why it matters: Only system messages have event data.
        """
        result = text_message.get_system_event_data()

        assert result is None

    def test_get_system_event_data_returns_none_for_invalid_json(
        self, db, group_conversation
    ):
        """
        get_system_event_data handles malformed JSON gracefully.

        Why it matters: Defensive programming - bad data shouldn't crash.
        """
        message = Message.objects.create(
            conversation=group_conversation,
            sender=None,
            message_type=MessageType.SYSTEM,
            content="not valid json",
        )

        result = message.get_system_event_data()

        assert result is None

    # -------------------------------------------------------------------------
    # String Representation
    # -------------------------------------------------------------------------

    def test_str_text_message_shows_sender(self, db, text_message):
        """
        Text message __str__ includes sender info.

        Why it matters: Debugging and admin panel clarity.
        """
        result = str(text_message)

        assert str(text_message.sender_id) in result

    def test_str_system_message_shows_system_indicator(self, db, system_message):
        """
        System message __str__ indicates no sender.

        Why it matters: Clear identification of system vs user messages.
        """
        result = str(system_message)

        assert "System" in result

    def test_str_deleted_message_shows_deleted_indicator(self, db, deleted_message):
        """
        Deleted message __str__ shows [deleted] indicator.

        Why it matters: Quick identification of deleted messages.
        """
        result = str(deleted_message)

        assert "[deleted]" in result

    def test_str_truncates_long_content(self, db, group_conversation, owner_user):
        """
        Long message content is truncated in __str__.

        Why it matters: Prevents excessively long strings in logs/admin.
        """
        long_content = "x" * 100
        message = Message.objects.create(
            conversation=group_conversation,
            sender=owner_user,
            message_type=MessageType.TEXT,
            content=long_content,
        )

        result = str(message)

        # Should be truncated to ~50 chars + "..."
        assert len(result) < len(long_content) + 50
        assert "..." in result
