"""
Tests for message editing functionality.

This module tests message editing following TDD principles:
- MessageService.edit_message() - Edit a message within time limit
- MessageService.get_edit_history() - Retrieve edit history

Test Organization:
    TestMessageEditService: Service layer tests for business logic
    TestMessageEditAPI: API endpoint tests for HTTP interface

Testing Philosophy:
    Tests focus on observable behavior:
    - ServiceResult success/failure states
    - Database state changes (edited_at, edit_count, original_content)
    - Edit history creation
    - Error codes for specific failure modes
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from authentication.tests.factories import UserFactory
from chat.constants import MESSAGE_CONFIG
from chat.models import (
    Message,
    MessageEditHistory,
    MessageType,
    Participant,
    ParticipantRole,
)
from chat.services import MessageService


# =============================================================================
# Test Fixtures for Message Editing
# =============================================================================


@pytest.fixture
def editable_message(db, group_conversation, owner_user):
    """
    Create a message that can be edited (within time limit).

    Returns a text message created by owner_user that is editable.
    """
    return Message.objects.create(
        conversation=group_conversation,
        sender=owner_user,
        message_type=MessageType.TEXT,
        content="Original message content",
    )


@pytest.fixture
def old_message(db, group_conversation, owner_user):
    """
    Create a message that is past the edit time limit.

    Returns a message that was created more than EDIT_TIME_LIMIT_SECONDS ago.
    """
    message = Message.objects.create(
        conversation=group_conversation,
        sender=owner_user,
        message_type=MessageType.TEXT,
        content="Old message content",
    )
    # Set created_at to past the edit limit
    past_time = timezone.now() - timedelta(
        seconds=MESSAGE_CONFIG.EDIT_TIME_LIMIT_SECONDS + 60
    )
    Message.objects.filter(pk=message.pk).update(created_at=past_time)
    message.refresh_from_db()
    return message


@pytest.fixture
def max_edited_message(db, group_conversation, owner_user):
    """
    Create a message that has reached the maximum edit count.

    Returns a message that cannot be edited further due to edit count limit.
    """
    return Message.objects.create(
        conversation=group_conversation,
        sender=owner_user,
        message_type=MessageType.TEXT,
        content="Max edited message",
        edit_count=MESSAGE_CONFIG.MAX_EDIT_COUNT,
        edited_at=timezone.now(),
        original_content="First version",
    )


@pytest.fixture
def message_with_edit_history(db, group_conversation, owner_user):
    """
    Create a message with existing edit history.

    Returns a dict with:
    - message: The message with edit history
    - history: List of MessageEditHistory entries
    """
    message = Message.objects.create(
        conversation=group_conversation,
        sender=owner_user,
        message_type=MessageType.TEXT,
        content="Current content after edits",
        edit_count=3,
        edited_at=timezone.now(),
        original_content="Original content",
    )

    history = []
    for i in range(3):
        entry = MessageEditHistory.objects.create(
            message=message,
            content=f"Content version {i}",
            edit_number=i + 1,
        )
        history.append(entry)

    return {"message": message, "history": history}


# =============================================================================
# TestMessageEditService
# =============================================================================


class TestMessageEditService:
    """
    Tests for MessageService.edit_message() business logic.

    Verifies:
    - Successful message editing
    - Original content preservation
    - Edit count tracking
    - Time limit enforcement
    - Authorization checks
    - Edit history creation
    """

    def test_edit_message_success(self, db, editable_message, owner_user):
        """
        Successfully edit a message with new content.

        Why it matters: Basic edit functionality - users need to fix typos
        or update messages shortly after sending.
        """
        result = MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="Updated message content",
        )

        assert result.success is True
        assert result.data.content == "Updated message content"

    def test_edit_message_preserves_original_content(
        self, db, editable_message, owner_user
    ):
        """
        First edit preserves original content in original_content field.

        Why it matters: Audit trail - need to see what message originally said.
        """
        original = editable_message.content

        MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="Edited content",
        )

        editable_message.refresh_from_db()
        assert editable_message.original_content == original

    def test_edit_message_does_not_overwrite_original_on_subsequent_edits(
        self, db, editable_message, owner_user
    ):
        """
        Subsequent edits do not overwrite original_content.

        Why it matters: original_content should always reflect the FIRST version,
        not the version before the most recent edit.
        """
        first_original = editable_message.content

        # First edit
        MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="First edit",
        )

        # Second edit
        MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="Second edit",
        )

        editable_message.refresh_from_db()
        assert editable_message.original_content == first_original

    def test_edit_message_increments_edit_count(self, db, editable_message, owner_user):
        """
        Each edit increments the edit_count field.

        Why it matters: Track how many times a message was modified
        for display and limit enforcement.
        """
        assert editable_message.edit_count == 0

        MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="Edit 1",
        )

        editable_message.refresh_from_db()
        assert editable_message.edit_count == 1

        MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="Edit 2",
        )

        editable_message.refresh_from_db()
        assert editable_message.edit_count == 2

    def test_edit_message_sets_edited_at_timestamp(
        self, db, editable_message, owner_user
    ):
        """
        Edit sets edited_at to current time.

        Why it matters: Display "(edited)" indicator in UI with timestamp.
        """
        assert editable_message.edited_at is None

        before_edit = timezone.now()
        MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="Edited content",
        )
        after_edit = timezone.now()

        editable_message.refresh_from_db()
        assert editable_message.edited_at is not None
        assert before_edit <= editable_message.edited_at <= after_edit

    def test_edit_message_fails_after_time_limit(self, db, old_message, owner_user):
        """
        Cannot edit message after EDIT_TIME_LIMIT_SECONDS has passed.

        Why it matters: Messages become permanent after the edit window
        to maintain conversation integrity.
        """
        result = MessageService.edit_message(
            user=owner_user,
            message_id=old_message.id,
            new_content="Too late to edit",
        )

        assert result.success is False
        assert result.error_code == "EDIT_TIME_EXPIRED"

    def test_edit_message_fails_if_not_author(
        self, db, editable_message, non_participant_user
    ):
        """
        Only the message author can edit their message.

        Why it matters: Users should only modify their own content.
        """
        other_user = UserFactory(email_verified=True)
        # Add them to the conversation so they're a participant
        Participant.objects.create(
            conversation=editable_message.conversation,
            user=other_user,
            role=ParticipantRole.MEMBER,
        )

        result = MessageService.edit_message(
            user=other_user,
            message_id=editable_message.id,
            new_content="Unauthorized edit",
        )

        assert result.success is False
        assert result.error_code == "NOT_AUTHOR"

    def test_edit_message_fails_if_max_edits_reached(
        self, db, max_edited_message, owner_user
    ):
        """
        Cannot edit message that has reached MAX_EDIT_COUNT.

        Why it matters: Prevent abuse - don't allow unlimited message rewrites.
        """
        result = MessageService.edit_message(
            user=owner_user,
            message_id=max_edited_message.id,
            new_content="One more edit",
        )

        assert result.success is False
        assert result.error_code == "MAX_EDITS_REACHED"

    def test_edit_message_fails_for_deleted_message(
        self, db, deleted_message, owner_user
    ):
        """
        Cannot edit a deleted message.

        Why it matters: Deleted messages should stay deleted.
        """
        result = MessageService.edit_message(
            user=owner_user,
            message_id=deleted_message.id,
            new_content="Edit deleted message",
        )

        assert result.success is False
        assert result.error_code == "MESSAGE_DELETED"

    def test_edit_message_fails_for_system_message(
        self, db, system_message, owner_user
    ):
        """
        Cannot edit system messages.

        Why it matters: System messages are auto-generated audit trail.
        """
        result = MessageService.edit_message(
            user=owner_user,
            message_id=system_message.id,
            new_content="Edit system message",
        )

        assert result.success is False
        assert result.error_code == "SYSTEM_MESSAGE"

    def test_edit_message_fails_for_nonexistent_message(self, db, owner_user):
        """
        Returns error for non-existent message ID.

        Why it matters: Graceful handling of invalid requests.
        """
        result = MessageService.edit_message(
            user=owner_user,
            message_id=99999,
            new_content="Edit nothing",
        )

        assert result.success is False
        assert result.error_code == "MESSAGE_NOT_FOUND"

    def test_edit_message_fails_with_empty_content(
        self, db, editable_message, owner_user
    ):
        """
        Cannot edit message to empty content.

        Why it matters: Messages must have content.
        """
        result = MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="",
        )

        assert result.success is False
        assert result.error_code == "EMPTY_CONTENT"

    def test_edit_message_fails_with_whitespace_only_content(
        self, db, editable_message, owner_user
    ):
        """
        Cannot edit message to whitespace-only content.

        Why it matters: Whitespace-only is effectively empty.
        """
        result = MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="   \n\t  ",
        )

        assert result.success is False
        assert result.error_code == "EMPTY_CONTENT"

    def test_edit_message_creates_history_entry(self, db, editable_message, owner_user):
        """
        Edit creates a MessageEditHistory entry.

        Why it matters: Full audit trail of all message versions.
        """
        original_content = editable_message.content

        MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="New content",
        )

        history = MessageEditHistory.objects.filter(message=editable_message)
        assert history.count() == 1
        assert history.first().content == original_content
        assert history.first().edit_number == 1

    def test_edit_creates_history_for_each_edit(self, db, editable_message, owner_user):
        """
        Each edit creates a separate history entry.

        Why it matters: Complete version history, not just original vs current.
        """
        # First edit
        MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="Version 2",
        )

        # Second edit
        MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="Version 3",
        )

        history = MessageEditHistory.objects.filter(message=editable_message).order_by(
            "edit_number"
        )
        assert history.count() == 2
        assert history[0].content == "Original message content"
        assert history[0].edit_number == 1
        assert history[1].content == "Version 2"
        assert history[1].edit_number == 2

    def test_edit_message_strips_whitespace(self, db, editable_message, owner_user):
        """
        Edit content is trimmed of leading/trailing whitespace.

        Why it matters: Consistent formatting.
        """
        result = MessageService.edit_message(
            user=owner_user,
            message_id=editable_message.id,
            new_content="  Trimmed content  ",
        )

        assert result.success is True
        assert result.data.content == "Trimmed content"


# =============================================================================
# TestGetEditHistory
# =============================================================================


class TestGetEditHistory:
    """
    Tests for MessageService.get_edit_history().

    Verifies:
    - Retrieving edit history
    - Authorization (participant access required)
    - Ordering of history entries
    """

    def test_get_edit_history_returns_all_edits(
        self, db, message_with_edit_history, owner_user
    ):
        """
        Returns all edit history entries for a message.

        Why it matters: Users want to see all previous versions.
        """
        message = message_with_edit_history["message"]

        result = MessageService.get_edit_history(
            user=owner_user,
            message_id=message.id,
        )

        assert result.success is True
        assert len(result.data) == 3

    def test_get_edit_history_ordered_by_created_at_desc(
        self, db, message_with_edit_history, owner_user
    ):
        """
        History entries are ordered by created_at descending (newest first).

        Why it matters: Most recent edit should be at the top.
        """
        message = message_with_edit_history["message"]

        result = MessageService.get_edit_history(
            user=owner_user,
            message_id=message.id,
        )

        timestamps = [entry.created_at for entry in result.data]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_get_edit_history_requires_participant_access(
        self, db, message_with_edit_history, non_participant_user
    ):
        """
        Only conversation participants can view edit history.

        Why it matters: Privacy - message history is conversation-private.
        """
        message = message_with_edit_history["message"]

        result = MessageService.get_edit_history(
            user=non_participant_user,
            message_id=message.id,
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"

    def test_get_edit_history_for_unedited_message(
        self, db, editable_message, owner_user
    ):
        """
        Returns empty list for message with no edits.

        Why it matters: Graceful handling - unedited messages have no history.
        """
        result = MessageService.get_edit_history(
            user=owner_user,
            message_id=editable_message.id,
        )

        assert result.success is True
        assert len(result.data) == 0

    def test_get_edit_history_for_nonexistent_message(self, db, owner_user):
        """
        Returns error for non-existent message.

        Why it matters: Clear error handling.
        """
        result = MessageService.get_edit_history(
            user=owner_user,
            message_id=99999,
        )

        assert result.success is False
        assert result.error_code == "MESSAGE_NOT_FOUND"

    def test_get_edit_history_includes_timestamp(
        self, db, message_with_edit_history, owner_user
    ):
        """
        Each history entry has a created_at timestamp.

        Why it matters: Display when each edit occurred.
        """
        message = message_with_edit_history["message"]

        result = MessageService.get_edit_history(
            user=owner_user,
            message_id=message.id,
        )

        for entry in result.data:
            assert entry.created_at is not None


# =============================================================================
# TestMessageEditAPI
# =============================================================================


@pytest.mark.django_db
class TestMessageEditAPI:
    """
    Tests for message editing API endpoints.

    Endpoints tested:
    - PATCH /api/v1/chat/conversations/{conversation_id}/messages/{id}/edit/ - Edit a message
    - GET /api/v1/chat/conversations/{conversation_id}/messages/{id}/history/ - Get edit history
    """

    def test_patch_message_edit_endpoint(
        self, owner_client, editable_message, group_conversation
    ):
        """
        PATCH request successfully edits message.

        Why it matters: Primary API for message editing.
        """
        response = owner_client.patch(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{editable_message.id}/edit/",
            {"content": "API edited content"},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["content"] == "API edited content"
        assert response.data["edit_count"] == 1
        assert response.data["edited_at"] is not None

    def test_patch_message_edit_unauthorized(
        self, non_participant_client, editable_message, group_conversation
    ):
        """
        Non-participant cannot edit message via API.

        Why it matters: Authorization enforcement at API level.
        """
        response = non_participant_client.patch(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{editable_message.id}/edit/",
            {"content": "Unauthorized edit"},
            format="json",
        )

        assert response.status_code == 403

    def test_patch_message_edit_not_author(
        self, member_client, editable_message, member_user, group_conversation
    ):
        """
        Participant who is not author cannot edit.

        Why it matters: Only authors can edit their messages.
        """
        # Add member to conversation
        Participant.objects.create(
            conversation=group_conversation,
            user=member_user,
            role=ParticipantRole.MEMBER,
        )

        response = member_client.patch(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{editable_message.id}/edit/",
            {"content": "Not my message"},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error_code"] == "NOT_AUTHOR"

    def test_patch_message_edit_expired(
        self, owner_client, old_message, group_conversation
    ):
        """
        Cannot edit message past time limit via API.

        Why it matters: Time limit enforcement at API level.
        """
        response = owner_client.patch(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{old_message.id}/edit/",
            {"content": "Too late"},
            format="json",
        )

        assert response.status_code == 400
        assert "time" in response.data["error"].lower()

    def test_get_edit_history_endpoint(
        self, owner_client, message_with_edit_history, group_conversation
    ):
        """
        GET request returns edit history.

        Why it matters: API to view message versions.
        """
        message = message_with_edit_history["message"]

        response = owner_client.get(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{message.id}/history/"
        )

        assert response.status_code == 200
        assert len(response.data) == 3
        # Verify history entries have expected fields
        for entry in response.data:
            assert "content" in entry
            assert "edit_number" in entry
            assert "created_at" in entry

    def test_get_edit_history_unauthorized(
        self, non_participant_client, message_with_edit_history, group_conversation
    ):
        """
        Non-participant cannot view edit history.

        Why it matters: Privacy at API level.
        """
        message = message_with_edit_history["message"]

        response = non_participant_client.get(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{message.id}/history/"
        )

        assert response.status_code == 403

    def test_edit_response_includes_edit_metadata(
        self, owner_client, editable_message, group_conversation
    ):
        """
        Edit response includes edited_at and edit_count.

        Why it matters: Client needs this data for UI updates.
        """
        response = owner_client.patch(
            f"/api/v1/chat/conversations/{group_conversation.id}/messages/{editable_message.id}/edit/",
            {"content": "Edited with metadata"},
            format="json",
        )

        assert response.status_code == 200
        assert "edited_at" in response.data
        assert "edit_count" in response.data
        assert response.data["edit_count"] == 1
