"""
Tests for ChatAuthorizationService and authorization decorators.

TDD: These tests are written BEFORE the authorization module implementation.
They define the expected behavior for service-level authorization checks.

Test Categories:
    1. is_conversation_participant - Basic participant checks
    2. is_message_author - Author verification
    3. can_access_message - Combined access checks with object return
    4. get_user_conversation_ids - Bulk conversation lookup
    5. get_participant_role - Role retrieval
    6. Decorator tests - require_conversation_participant, require_message_access
"""

from django.utils import timezone

from authentication.tests.factories import UserFactory
from chat.models import (
    Conversation,
    ConversationType,
    Participant,
    ParticipantRole,
)
from core.services import BaseService, ServiceResult


# =============================================================================
# Test is_conversation_participant
# =============================================================================


class TestIsConversationParticipant:
    """Tests for ChatAuthorizationService.is_conversation_participant()."""

    def test_returns_true_for_active_participant(
        self, db, group_conversation, owner_user
    ):
        """Active participant (left_at=NULL) returns True."""
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_conversation_participant(
            owner_user, group_conversation.id
        )
        assert result is True

    def test_returns_false_for_non_participant(
        self, db, group_conversation, non_participant_user
    ):
        """User with no Participant record returns False."""
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_conversation_participant(
            non_participant_user, group_conversation.id
        )
        assert result is False

    def test_returns_false_for_left_participant(self, db, group_conversation):
        """Participant who has left (left_at set) returns False."""
        from chat.authorization import ChatAuthorizationService

        user = UserFactory(email_verified=True)
        Participant.objects.create(
            conversation=group_conversation,
            user=user,
            role=ParticipantRole.MEMBER,
            left_at=timezone.now(),
            left_voluntarily=True,
        )

        result = ChatAuthorizationService.is_conversation_participant(
            user, group_conversation.id
        )
        assert result is False

    def test_returns_true_for_deleted_conversation_participant(
        self, db, deleted_conversation, owner_user
    ):
        """
        Participant in soft-deleted conversation still returns True.

        Note: Soft-deleted conversations still have active participants.
        The conversation-level delete check should happen separately.
        """
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_conversation_participant(
            owner_user, deleted_conversation.id
        )
        # Still True because participant record exists and is active
        assert result is True

    def test_returns_false_for_nonexistent_conversation(self, db, owner_user):
        """Non-existent conversation ID returns False."""
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_conversation_participant(
            owner_user, 999999
        )
        assert result is False

    def test_works_with_direct_conversation(self, db, direct_conversation, owner_user):
        """Works correctly for direct conversation participants."""
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_conversation_participant(
            owner_user, direct_conversation.id
        )
        assert result is True


# =============================================================================
# Test is_message_author
# =============================================================================


class TestIsMessageAuthor:
    """Tests for ChatAuthorizationService.is_message_author()."""

    def test_returns_true_for_message_sender(self, db, text_message, owner_user):
        """Message sender returns True."""
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_message_author(owner_user, text_message.id)
        assert result is True

    def test_returns_false_for_non_sender(self, db, text_message, member_user):
        """Non-sender returns False."""
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_message_author(
            member_user, text_message.id
        )
        assert result is False

    def test_returns_false_for_system_message(self, db, system_message, owner_user):
        """System messages have no sender, returns False."""
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_message_author(
            owner_user, system_message.id
        )
        assert result is False

    def test_returns_false_for_nonexistent_message(self, db, owner_user):
        """Non-existent message ID returns False."""
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_message_author(owner_user, 999999)
        assert result is False

    def test_returns_true_for_deleted_message_author(
        self, db, deleted_message, owner_user
    ):
        """
        Author of soft-deleted message still returns True.

        Note: Author check doesn't consider soft-delete status.
        Deletion-aware checks use can_access_message.
        """
        from chat.authorization import ChatAuthorizationService

        result = ChatAuthorizationService.is_message_author(
            owner_user, deleted_message.id
        )
        assert result is True


# =============================================================================
# Test can_access_message
# =============================================================================


class TestCanAccessMessage:
    """Tests for ChatAuthorizationService.can_access_message()."""

    def test_returns_true_and_message_for_valid_participant(
        self, db, text_message, owner_user
    ):
        """Participant can access message in their conversation."""
        from chat.authorization import ChatAuthorizationService

        can_access, message = ChatAuthorizationService.can_access_message(
            owner_user, text_message.id
        )

        assert can_access is True
        assert message is not None
        assert message.id == text_message.id

    def test_returns_false_for_non_participant(
        self, db, text_message, non_participant_user
    ):
        """Non-participant cannot access message."""
        from chat.authorization import ChatAuthorizationService

        can_access, message = ChatAuthorizationService.can_access_message(
            non_participant_user, text_message.id
        )

        assert can_access is False
        assert message is None

    def test_returns_false_for_soft_deleted_message(
        self, db, deleted_message, owner_user
    ):
        """Soft-deleted message returns False."""
        from chat.authorization import ChatAuthorizationService

        can_access, message = ChatAuthorizationService.can_access_message(
            owner_user, deleted_message.id
        )

        assert can_access is False
        assert message is None

    def test_returns_false_for_nonexistent_message(self, db, owner_user):
        """Non-existent message returns False."""
        from chat.authorization import ChatAuthorizationService

        can_access, message = ChatAuthorizationService.can_access_message(
            owner_user, 999999
        )

        assert can_access is False
        assert message is None

    def test_returns_message_with_conversation_prefetched(
        self, db, text_message, owner_user
    ):
        """Returned message should have conversation relationship loaded."""
        from chat.authorization import ChatAuthorizationService

        can_access, message = ChatAuthorizationService.can_access_message(
            owner_user, text_message.id
        )

        assert can_access is True
        # Should not trigger additional query
        assert message.conversation is not None
        assert message.conversation.id == text_message.conversation_id


# =============================================================================
# Test get_user_conversation_ids
# =============================================================================


class TestGetUserConversationIds:
    """Tests for ChatAuthorizationService.get_user_conversation_ids()."""

    def test_returns_list_of_active_conversation_ids(
        self, db, group_conversation, direct_conversation, owner_user
    ):
        """Returns IDs for all active participations."""
        from chat.authorization import ChatAuthorizationService

        ids = ChatAuthorizationService.get_user_conversation_ids(owner_user)

        assert group_conversation.id in ids
        assert direct_conversation.id in ids
        assert len(ids) >= 2

    def test_excludes_left_conversations(self, db, owner_user):
        """Does not include conversations user has left."""
        from chat.authorization import ChatAuthorizationService

        # Create conversation and leave it
        conversation = Conversation.objects.create(
            conversation_type=ConversationType.GROUP,
            title="Left Group",
            created_by=owner_user,
        )
        Participant.objects.create(
            conversation=conversation,
            user=owner_user,
            role=ParticipantRole.OWNER,
            left_at=timezone.now(),
            left_voluntarily=True,
        )

        ids = ChatAuthorizationService.get_user_conversation_ids(owner_user)
        assert conversation.id not in ids

    def test_returns_empty_list_for_no_participations(self, db, non_participant_user):
        """Returns empty list for user with no participations."""
        from chat.authorization import ChatAuthorizationService

        ids = ChatAuthorizationService.get_user_conversation_ids(non_participant_user)
        assert ids == []

    def test_includes_direct_conversations(self, db, direct_conversation, owner_user):
        """Includes direct conversation IDs."""
        from chat.authorization import ChatAuthorizationService

        ids = ChatAuthorizationService.get_user_conversation_ids(owner_user)
        assert direct_conversation.id in ids


# =============================================================================
# Test get_participant_role
# =============================================================================


class TestGetParticipantRole:
    """Tests for ChatAuthorizationService.get_participant_role()."""

    def test_returns_owner_role(self, db, group_conversation_with_members, owner_user):
        """Returns 'owner' for conversation owner."""
        from chat.authorization import ChatAuthorizationService

        role = ChatAuthorizationService.get_participant_role(
            owner_user, group_conversation_with_members.id
        )
        assert role == ParticipantRole.OWNER

    def test_returns_admin_role(self, db, group_conversation_with_members, admin_user):
        """Returns 'admin' for conversation admin."""
        from chat.authorization import ChatAuthorizationService

        role = ChatAuthorizationService.get_participant_role(
            admin_user, group_conversation_with_members.id
        )
        assert role == ParticipantRole.ADMIN

    def test_returns_member_role(
        self, db, group_conversation_with_members, member_user
    ):
        """Returns 'member' for conversation member."""
        from chat.authorization import ChatAuthorizationService

        role = ChatAuthorizationService.get_participant_role(
            member_user, group_conversation_with_members.id
        )
        assert role == ParticipantRole.MEMBER

    def test_returns_none_for_non_participant(
        self, db, group_conversation, non_participant_user
    ):
        """Returns None for user not in conversation."""
        from chat.authorization import ChatAuthorizationService

        role = ChatAuthorizationService.get_participant_role(
            non_participant_user, group_conversation.id
        )
        assert role is None

    def test_returns_none_for_direct_conversation(
        self, db, direct_conversation, owner_user
    ):
        """Direct conversations have role=None for participants."""
        from chat.authorization import ChatAuthorizationService

        role = ChatAuthorizationService.get_participant_role(
            owner_user, direct_conversation.id
        )
        # Direct conversation participants have role=None
        assert role is None


# =============================================================================
# Test require_conversation_participant decorator
# =============================================================================


class TestRequireConversationParticipantDecorator:
    """Tests for @require_conversation_participant decorator."""

    def test_allows_participant_through(self, db, group_conversation, owner_user):
        """Participant can call decorated method."""
        from chat.authorization import require_conversation_participant

        class TestService(BaseService):
            @classmethod
            @require_conversation_participant()
            def test_method(cls, user, conversation_id):
                return ServiceResult.success("allowed")

        result = TestService.test_method(
            user=owner_user, conversation_id=group_conversation.id
        )

        assert result.success is True
        assert result.data == "allowed"

    def test_blocks_non_participant(self, db, group_conversation, non_participant_user):
        """Non-participant gets ServiceResult.failure."""
        from chat.authorization import require_conversation_participant

        class TestService(BaseService):
            @classmethod
            @require_conversation_participant()
            def test_method(cls, user, conversation_id):
                return ServiceResult.success("allowed")

        result = TestService.test_method(
            user=non_participant_user, conversation_id=group_conversation.id
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"

    def test_uses_custom_parameter_name(self, db, group_conversation, owner_user):
        """Can use custom parameter name for conversation_id."""
        from chat.authorization import require_conversation_participant

        class TestService(BaseService):
            @classmethod
            @require_conversation_participant(conversation_id_param="conv_id")
            def test_method(cls, user, conv_id):
                return ServiceResult.success("allowed")

        result = TestService.test_method(user=owner_user, conv_id=group_conversation.id)

        assert result.success is True

    def test_returns_error_for_missing_user(self, db, group_conversation):
        """Returns error if user parameter is missing."""
        from chat.authorization import require_conversation_participant

        class TestService(BaseService):
            @classmethod
            @require_conversation_participant()
            def test_method(cls, user, conversation_id):
                return ServiceResult.success("allowed")

        result = TestService.test_method(
            user=None, conversation_id=group_conversation.id
        )

        assert result.success is False
        assert result.error_code == "INVALID_REQUEST"

    def test_returns_error_for_missing_conversation_id(self, db, owner_user):
        """Returns error if conversation_id parameter is missing."""
        from chat.authorization import require_conversation_participant

        class TestService(BaseService):
            @classmethod
            @require_conversation_participant()
            def test_method(cls, user, conversation_id):
                return ServiceResult.success("allowed")

        result = TestService.test_method(user=owner_user, conversation_id=None)

        assert result.success is False
        assert result.error_code == "INVALID_REQUEST"


# =============================================================================
# Test require_message_access decorator
# =============================================================================


class TestRequireMessageAccessDecorator:
    """Tests for @require_message_access decorator."""

    def test_allows_access_and_injects_message(self, db, text_message, owner_user):
        """Authorized user gets _message injected."""
        from chat.authorization import require_message_access

        class TestService(BaseService):
            @classmethod
            @require_message_access()
            def test_method(cls, user, message_id, _message=None):
                return ServiceResult.success(_message)

        result = TestService.test_method(user=owner_user, message_id=text_message.id)

        assert result.success is True
        assert result.data is not None
        assert result.data.id == text_message.id

    def test_blocks_non_participant(self, db, text_message, non_participant_user):
        """Non-participant gets NOT_PARTICIPANT error."""
        from chat.authorization import require_message_access

        class TestService(BaseService):
            @classmethod
            @require_message_access()
            def test_method(cls, user, message_id, _message=None):
                return ServiceResult.success(_message)

        result = TestService.test_method(
            user=non_participant_user, message_id=text_message.id
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"

    def test_returns_not_found_for_missing_message(self, db, owner_user):
        """Non-existent message returns MESSAGE_NOT_FOUND."""
        from chat.authorization import require_message_access

        class TestService(BaseService):
            @classmethod
            @require_message_access()
            def test_method(cls, user, message_id, _message=None):
                return ServiceResult.success(_message)

        result = TestService.test_method(user=owner_user, message_id=999999)

        assert result.success is False
        assert result.error_code == "MESSAGE_NOT_FOUND"

    def test_returns_not_found_for_deleted_message(
        self, db, deleted_message, owner_user
    ):
        """Soft-deleted message returns MESSAGE_NOT_FOUND."""
        from chat.authorization import require_message_access

        class TestService(BaseService):
            @classmethod
            @require_message_access()
            def test_method(cls, user, message_id, _message=None):
                return ServiceResult.success(_message)

        result = TestService.test_method(user=owner_user, message_id=deleted_message.id)

        assert result.success is False
        assert result.error_code == "MESSAGE_NOT_FOUND"

    def test_uses_custom_parameter_name(self, db, text_message, owner_user):
        """Can use custom parameter name for message_id."""
        from chat.authorization import require_message_access

        class TestService(BaseService):
            @classmethod
            @require_message_access(message_id_param="msg_id")
            def test_method(cls, user, msg_id, _message=None):
                return ServiceResult.success(_message)

        result = TestService.test_method(user=owner_user, msg_id=text_message.id)

        assert result.success is True
        assert result.data.id == text_message.id

    def test_returns_error_for_missing_user(self, db, text_message):
        """Returns error if user parameter is missing."""
        from chat.authorization import require_message_access

        class TestService(BaseService):
            @classmethod
            @require_message_access()
            def test_method(cls, user, message_id, _message=None):
                return ServiceResult.success(_message)

        result = TestService.test_method(user=None, message_id=text_message.id)

        assert result.success is False
        assert result.error_code == "INVALID_REQUEST"

    def test_returns_error_for_missing_message_id(self, db, owner_user):
        """Returns error if message_id parameter is missing."""
        from chat.authorization import require_message_access

        class TestService(BaseService):
            @classmethod
            @require_message_access()
            def test_method(cls, user, message_id, _message=None):
                return ServiceResult.success(_message)

        result = TestService.test_method(user=owner_user, message_id=None)

        assert result.success is False
        assert result.error_code == "INVALID_REQUEST"

    def test_injected_message_has_conversation_loaded(
        self, db, text_message, owner_user
    ):
        """Injected message should have conversation prefetched."""
        from chat.authorization import require_message_access

        class TestService(BaseService):
            @classmethod
            @require_message_access()
            def test_method(cls, user, message_id, _message=None):
                # Access conversation without additional query
                conv_id = _message.conversation.id
                return ServiceResult.success(conv_id)

        result = TestService.test_method(user=owner_user, message_id=text_message.id)

        assert result.success is True
        assert result.data == text_message.conversation_id
