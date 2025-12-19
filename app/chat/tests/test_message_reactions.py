"""
Tests for message reactions feature.

This module tests the ReactionService which handles:
- Adding reactions to messages
- Removing reactions from messages
- Toggle reaction (add/remove)
- Getting reactions for a message
- Getting user reactions for multiple messages
- Atomic count updates in Message.reaction_counts

TDD approach: These tests are written first to drive implementation.
"""

import pytest

from authentication.tests.factories import UserFactory
from chat.constants import REACTION_CONFIG
from chat.models import (
    ConversationType,
    Message,
    MessageReaction,
    MessageType,
    ParticipantRole,
)
from chat.services import ReactionService
from chat.tests.factories import (
    ConversationFactory,
    MessageFactory,
    ParticipantFactory,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def owner_user(db):
    """User who owns the conversation."""
    return UserFactory()


@pytest.fixture
def member_user(db):
    """User who is a member of the conversation."""
    return UserFactory()


@pytest.fixture
def non_participant_user(db):
    """User who is not part of the conversation."""
    return UserFactory()


@pytest.fixture
def group_conversation(db, owner_user):
    """Group conversation with owner as participant."""
    conversation = ConversationFactory(
        conversation_type=ConversationType.GROUP,
        title="Test Group",
    )
    ParticipantFactory(
        conversation=conversation,
        user=owner_user,
        role=ParticipantRole.OWNER,
    )
    return conversation


@pytest.fixture
def message(db, group_conversation, owner_user):
    """A message in the conversation."""
    return Message.objects.create(
        conversation=group_conversation,
        sender=owner_user,
        message_type=MessageType.TEXT,
        content="Test message for reactions",
    )


@pytest.fixture
def member_participant(db, group_conversation, member_user):
    """Add member to the conversation."""
    return ParticipantFactory(
        conversation=group_conversation,
        user=member_user,
        role=ParticipantRole.MEMBER,
    )


# =============================================================================
# TestReactionService - Add Reaction
# =============================================================================


@pytest.mark.django_db
class TestAddReaction:
    """Tests for ReactionService.add_reaction method."""

    def test_add_reaction_success(self, message, owner_user):
        """
        Adding a reaction creates a MessageReaction record.

        Why it matters: Core functionality for adding reactions.
        """
        result = ReactionService.add_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="👍",
        )

        assert result.success is True
        assert result.data is not None
        assert result.data.emoji == "👍"
        assert result.data.user_id == owner_user.id
        assert result.data.message_id == message.id

    def test_add_reaction_updates_count_atomically(
        self, message, owner_user, member_user, member_participant
    ):
        """
        Adding reactions updates Message.reaction_counts atomically.

        Why it matters: Prevents race conditions in reaction counts.
        """
        # Add first reaction
        ReactionService.add_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="👍",
        )

        message.refresh_from_db()
        assert message.reaction_counts.get("👍") == 1

        # Add second reaction with same emoji from different user
        ReactionService.add_reaction(
            user=member_user,
            message_id=message.id,
            emoji="👍",
        )

        message.refresh_from_db()
        assert message.reaction_counts.get("👍") == 2

    def test_add_reaction_fails_if_not_participant(self, message, non_participant_user):
        """
        Non-participants cannot add reactions.

        Why it matters: Authorization at service level.
        """
        result = ReactionService.add_reaction(
            user=non_participant_user,
            message_id=message.id,
            emoji="👍",
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"

    def test_add_reaction_fails_invalid_emoji(self, message, owner_user):
        """
        Invalid emoji strings are rejected.

        Why it matters: Data validation for emoji field.
        """
        # Test with empty string
        result = ReactionService.add_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="",
        )

        assert result.success is False
        assert result.error_code == "INVALID_EMOJI"

    def test_add_reaction_duplicate_returns_existing(self, message, owner_user):
        """
        Adding same reaction twice returns the existing one.

        Why it matters: Idempotent behavior for duplicate reactions.
        """
        # First reaction
        result1 = ReactionService.add_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="👍",
        )
        assert result1.success is True
        first_id = result1.data.id

        # Second attempt - same emoji, same user
        result2 = ReactionService.add_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="👍",
        )

        # Should return the existing reaction
        assert result2.success is True
        assert result2.data.id == first_id

        # Count should still be 1
        message.refresh_from_db()
        assert message.reaction_counts.get("👍") == 1

    def test_add_multiple_different_emojis_same_user(self, message, owner_user):
        """
        User can add multiple different emoji reactions to same message.

        Why it matters: Model allows multiple emojis per user per message.
        """
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="👍")
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="❤️")
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="🎉")

        message.refresh_from_db()
        assert message.reaction_counts.get("👍") == 1
        assert message.reaction_counts.get("❤️") == 1
        assert message.reaction_counts.get("🎉") == 1

        # Verify reactions in database
        reactions = MessageReaction.objects.filter(message=message, user=owner_user)
        assert reactions.count() == 3

    def test_add_reaction_fails_for_nonexistent_message(self, owner_user):
        """
        Adding reaction to non-existent message fails.

        Why it matters: Proper error handling for invalid message ID.
        """
        result = ReactionService.add_reaction(
            user=owner_user,
            message_id=99999,
            emoji="👍",
        )

        assert result.success is False
        assert result.error_code == "MESSAGE_NOT_FOUND"

    def test_add_reaction_fails_for_deleted_message(self, message, owner_user):
        """
        Cannot react to a soft-deleted message.

        Why it matters: Business rule for deleted messages.
        """
        message.soft_delete()

        result = ReactionService.add_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="👍",
        )

        assert result.success is False
        assert result.error_code == "MESSAGE_DELETED"


# =============================================================================
# TestReactionService - Remove Reaction
# =============================================================================


@pytest.mark.django_db
class TestRemoveReaction:
    """Tests for ReactionService.remove_reaction method."""

    def test_remove_reaction_success(self, message, owner_user):
        """
        Removing a reaction deletes the MessageReaction record.

        Why it matters: Core functionality for removing reactions.
        """
        # Add reaction first
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="👍")
        assert MessageReaction.objects.filter(
            message=message, user=owner_user, emoji="👍"
        ).exists()

        # Remove reaction
        result = ReactionService.remove_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="👍",
        )

        assert result.success is True
        assert not MessageReaction.objects.filter(
            message=message, user=owner_user, emoji="👍"
        ).exists()

    def test_remove_reaction_decrements_count(
        self, message, owner_user, member_user, member_participant
    ):
        """
        Removing a reaction decrements Message.reaction_counts.

        Why it matters: Count accuracy after removal.
        """
        # Add reactions
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="👍")
        ReactionService.add_reaction(
            user=member_user, message_id=message.id, emoji="👍"
        )

        message.refresh_from_db()
        assert message.reaction_counts.get("👍") == 2

        # Remove one reaction
        ReactionService.remove_reaction(
            user=owner_user, message_id=message.id, emoji="👍"
        )

        message.refresh_from_db()
        assert message.reaction_counts.get("👍") == 1

    def test_remove_reaction_removes_emoji_key_at_zero(self, message, owner_user):
        """
        When count reaches 0, the emoji key is removed from reaction_counts.

        Why it matters: Clean data structure without stale keys.
        """
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="👍")

        message.refresh_from_db()
        assert "👍" in message.reaction_counts

        ReactionService.remove_reaction(
            user=owner_user, message_id=message.id, emoji="👍"
        )

        message.refresh_from_db()
        assert "👍" not in message.reaction_counts

    def test_remove_reaction_not_found(self, message, owner_user):
        """
        Removing non-existent reaction returns appropriate error.

        Why it matters: Proper error handling for invalid removal.
        """
        result = ReactionService.remove_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="👍",
        )

        assert result.success is False
        assert result.error_code == "REACTION_NOT_FOUND"

    def test_remove_reaction_fails_if_not_participant(
        self, message, non_participant_user
    ):
        """
        Non-participants cannot remove reactions.

        Why it matters: Authorization check.
        """
        result = ReactionService.remove_reaction(
            user=non_participant_user,
            message_id=message.id,
            emoji="👍",
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"


# =============================================================================
# TestReactionService - Toggle Reaction
# =============================================================================


@pytest.mark.django_db
class TestToggleReaction:
    """Tests for ReactionService.toggle_reaction method."""

    def test_toggle_reaction_adds_when_missing(self, message, owner_user):
        """
        Toggle adds reaction when it doesn't exist.

        Why it matters: Convenient toggle functionality for UI.
        """
        result = ReactionService.toggle_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="👍",
        )

        assert result.success is True
        added, reaction = result.data
        assert added is True
        assert reaction is not None
        assert reaction.emoji == "👍"

    def test_toggle_reaction_removes_when_exists(self, message, owner_user):
        """
        Toggle removes reaction when it exists.

        Why it matters: Convenient toggle functionality for UI.
        """
        # Add reaction first
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="👍")

        # Toggle should remove
        result = ReactionService.toggle_reaction(
            user=owner_user,
            message_id=message.id,
            emoji="👍",
        )

        assert result.success is True
        added, reaction = result.data
        assert added is False
        assert reaction is None

        # Verify removed
        assert not MessageReaction.objects.filter(
            message=message, user=owner_user, emoji="👍"
        ).exists()

    def test_toggle_reaction_fails_if_not_participant(
        self, message, non_participant_user
    ):
        """
        Non-participants cannot toggle reactions.

        Why it matters: Authorization check.
        """
        result = ReactionService.toggle_reaction(
            user=non_participant_user,
            message_id=message.id,
            emoji="👍",
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"


# =============================================================================
# TestReactionService - Get Reactions
# =============================================================================


@pytest.mark.django_db
class TestGetReactions:
    """Tests for ReactionService.get_message_reactions method."""

    def test_get_reactions_for_message(
        self, message, owner_user, member_user, member_participant
    ):
        """
        Get all reactions grouped by emoji with user lists.

        Why it matters: Display reactions with who reacted.
        """
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="👍")
        ReactionService.add_reaction(
            user=member_user, message_id=message.id, emoji="👍"
        )
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="❤️")

        result = ReactionService.get_message_reactions(message_id=message.id)

        assert result.success is True
        data = result.data
        assert "👍" in data
        assert "❤️" in data
        assert len(data["👍"]["users"]) == 2
        assert len(data["❤️"]["users"]) == 1

    def test_get_reactions_includes_count(
        self, message, owner_user, member_user, member_participant
    ):
        """
        Reaction data includes count for each emoji.

        Why it matters: Quick access to count without counting users array.
        """
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="👍")
        ReactionService.add_reaction(
            user=member_user, message_id=message.id, emoji="👍"
        )

        result = ReactionService.get_message_reactions(message_id=message.id)

        assert result.success is True
        assert result.data["👍"]["count"] == 2

    def test_get_reactions_empty_message(self, message):
        """
        Message with no reactions returns empty dict.

        Why it matters: Proper handling of no reactions.
        """
        result = ReactionService.get_message_reactions(message_id=message.id)

        assert result.success is True
        assert result.data == {}

    def test_get_reactions_nonexistent_message(self):
        """
        Non-existent message returns error.

        Why it matters: Proper error handling.
        """
        result = ReactionService.get_message_reactions(message_id=99999)

        assert result.success is False
        assert result.error_code == "MESSAGE_NOT_FOUND"


# =============================================================================
# TestReactionService - Get User Reactions
# =============================================================================


@pytest.mark.django_db
class TestGetUserReactions:
    """Tests for ReactionService.get_user_reactions method."""

    def test_get_user_reactions(self, group_conversation, owner_user):
        """
        Get user's reactions for multiple messages.

        Why it matters: Client needs to know which emojis user has used.
        """
        # Create multiple messages
        msg1 = MessageFactory(conversation=group_conversation, sender=owner_user)
        msg2 = MessageFactory(conversation=group_conversation, sender=owner_user)
        msg3 = MessageFactory(conversation=group_conversation, sender=owner_user)

        # Add reactions
        ReactionService.add_reaction(user=owner_user, message_id=msg1.id, emoji="👍")
        ReactionService.add_reaction(user=owner_user, message_id=msg1.id, emoji="❤️")
        ReactionService.add_reaction(user=owner_user, message_id=msg2.id, emoji="🎉")
        # msg3 has no reactions

        result = ReactionService.get_user_reactions(
            user=owner_user,
            message_ids=[msg1.id, msg2.id, msg3.id],
        )

        assert result.success is True
        data = result.data
        assert msg1.id in data
        assert set(data[msg1.id]) == {"👍", "❤️"}
        assert msg2.id in data
        assert set(data[msg2.id]) == {"🎉"}
        # msg3 should not be in results (no reactions)
        assert msg3.id not in data

    def test_get_user_reactions_empty_list(self, owner_user):
        """
        Empty message ID list returns empty dict.

        Why it matters: Edge case handling.
        """
        result = ReactionService.get_user_reactions(
            user=owner_user,
            message_ids=[],
        )

        assert result.success is True
        assert result.data == {}


# =============================================================================
# TestReactionService - Limits
# =============================================================================


@pytest.mark.django_db
class TestReactionLimits:
    """Tests for reaction limits from REACTION_CONFIG."""

    def test_max_user_reactions_per_message(self, message, owner_user):
        """
        User cannot exceed MAX_USER_REACTIONS_PER_MESSAGE.

        Why it matters: Prevents reaction spam.
        """
        max_reactions = REACTION_CONFIG.MAX_USER_REACTIONS_PER_MESSAGE
        emojis = ["👍", "❤️", "😂", "😮", "😢", "🎉", "👎", "🔥", "💯", "🙏"]

        # Add up to the limit
        for i in range(max_reactions):
            result = ReactionService.add_reaction(
                user=owner_user,
                message_id=message.id,
                emoji=emojis[i],
            )
            assert result.success is True

        # Next one should fail
        result = ReactionService.add_reaction(
            user=owner_user,
            message_id=message.id,
            emoji=emojis[max_reactions],
        )

        assert result.success is False
        assert result.error_code == "MAX_REACTIONS_EXCEEDED"


# =============================================================================
# TestReactionAPI
# =============================================================================


@pytest.fixture
def owner_client(owner_user):
    """API client authenticated as owner."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=owner_user)
    return client


@pytest.fixture
def member_client(member_user):
    """API client authenticated as member."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=member_user)
    return client


@pytest.fixture
def non_participant_client(non_participant_user):
    """API client authenticated as non-participant."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=non_participant_user)
    return client


@pytest.mark.django_db
class TestReactionAPI:
    """
    Tests for reaction API endpoints.

    Endpoints tested:
    - POST /api/v1/chat/conversations/{conv_id}/messages/{msg_id}/reactions/ - Add reaction
    - DELETE /api/v1/chat/conversations/{conv_id}/messages/{msg_id}/reactions/{emoji}/ - Remove reaction
    - POST /api/v1/chat/conversations/{conv_id}/messages/{msg_id}/reactions/toggle/ - Toggle reaction
    - GET /api/v1/chat/conversations/{conv_id}/messages/{msg_id}/reactions/ - Get reactions
    """

    def test_post_reaction_endpoint(self, owner_client, message, group_conversation):
        """
        POST request successfully adds reaction.

        Why it matters: Primary API for adding reactions.
        """
        response = owner_client.post(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{message.id}/reactions/",
            {"emoji": "👍"},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["emoji"] == "👍"

    def test_delete_reaction_endpoint(
        self, owner_client, message, group_conversation, owner_user
    ):
        """
        DELETE request successfully removes reaction.

        Why it matters: API for removing reactions.
        """
        # Add reaction first
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="👍")

        response = owner_client.delete(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{message.id}/reactions/%F0%9F%91%8D/",
        )

        assert response.status_code == 204

    def test_toggle_reaction_endpoint(self, owner_client, message, group_conversation):
        """
        POST toggle request toggles reaction.

        Why it matters: Convenient toggle API.
        """
        # First toggle - should add
        response = owner_client.post(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{message.id}/reactions/toggle/",
            {"emoji": "👍"},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["added"] is True

        # Second toggle - should remove
        response = owner_client.post(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{message.id}/reactions/toggle/",
            {"emoji": "👍"},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["added"] is False

    def test_get_reactions_endpoint(
        self,
        owner_client,
        message,
        group_conversation,
        owner_user,
        member_user,
        member_participant,
    ):
        """
        GET request returns reactions grouped by emoji.

        Why it matters: API to retrieve reaction data for UI.
        """
        ReactionService.add_reaction(user=owner_user, message_id=message.id, emoji="👍")
        ReactionService.add_reaction(
            user=member_user, message_id=message.id, emoji="👍"
        )

        response = owner_client.get(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{message.id}/reactions/",
        )

        assert response.status_code == 200
        assert "👍" in response.data
        assert response.data["👍"]["count"] == 2

    def test_reaction_unauthorized(
        self, non_participant_client, message, group_conversation
    ):
        """
        Non-participant cannot add reactions via API.

        Why it matters: Authorization at API level.
        """
        response = non_participant_client.post(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{message.id}/reactions/",
            {"emoji": "👍"},
            format="json",
        )

        assert response.status_code == 403
