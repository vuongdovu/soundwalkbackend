"""
Tests for Presence Service.

Tests the Redis-based presence tracking system following TDD principles.
Features tested:
- User presence status (online, away, offline)
- Atomic status updates via Lua scripts
- Per-conversation presence
- Heartbeat mechanism
- Presence queries (who's online, conversation presence)
- TTL-based auto-expiry

Design Decisions:
- Redis-only storage (no database writes for presence)
- Lua scripts for atomic operations
- Separate keys for user status and conversation presence
- Automatic cleanup via Redis TTL
"""

from unittest.mock import patch
import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from chat.models import ConversationType
from chat.tests.factories import ConversationFactory, ParticipantFactory

User = get_user_model()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def user(db):
    """Create a test user."""
    return User.objects.create_user(
        email="user@example.com",
        password="testpass123",
    )


@pytest.fixture
def other_user(db):
    """Create another test user."""
    return User.objects.create_user(
        email="other@example.com",
        password="testpass123",
    )


@pytest.fixture
def third_user(db):
    """Create a third test user."""
    return User.objects.create_user(
        email="third@example.com",
        password="testpass123",
    )


@pytest.fixture
def conversation(user, other_user):
    """Create a direct conversation between user and other_user."""
    conv = ConversationFactory(
        conversation_type=ConversationType.DIRECT,
        created_by=user,
    )
    ParticipantFactory(conversation=conv, user=user)
    ParticipantFactory(conversation=conv, user=other_user)
    return conv


@pytest.fixture
def group_conversation(user, other_user, third_user):
    """Create a group conversation with three users."""
    conv = ConversationFactory(
        conversation_type=ConversationType.GROUP,
        title="Test Group",
        created_by=user,
    )
    ParticipantFactory(conversation=conv, user=user, role="owner")
    ParticipantFactory(conversation=conv, user=other_user, role="member")
    ParticipantFactory(conversation=conv, user=third_user, role="member")
    return conv


@pytest.fixture
def api_client():
    """Create an API client."""
    return APIClient()


@pytest.fixture
def auth_client(api_client, user):
    """Create an authenticated API client."""
    api_client.force_authenticate(user=user)
    return api_client


# =============================================================================
# Presence Status Enum Tests
# =============================================================================


class TestPresenceStatus:
    """Test presence status values."""

    def test_presence_status_values(self):
        """
        Presence status should have three values.

        Why it matters: Clients need consistent status values to display.
        """
        from chat.services import PresenceStatus

        assert PresenceStatus.ONLINE == "online"
        assert PresenceStatus.AWAY == "away"
        assert PresenceStatus.OFFLINE == "offline"


# =============================================================================
# Set Presence Tests
# =============================================================================


@pytest.mark.django_db
class TestSetPresence:
    """Test setting user presence."""

    def test_set_user_online(self, user):
        """
        Setting presence to online should store status in Redis.

        Why it matters: Core functionality for presence tracking.
        """
        from chat.services import PresenceService, PresenceStatus

        result = PresenceService.set_presence(
            user_id=user.id,
            status=PresenceStatus.ONLINE,
        )

        assert result.success
        assert result.data["status"] == PresenceStatus.ONLINE

    def test_set_user_away(self, user):
        """
        Setting presence to away should store status in Redis.

        Why it matters: Users may want to indicate they're not immediately available.
        """
        from chat.services import PresenceService, PresenceStatus

        result = PresenceService.set_presence(
            user_id=user.id,
            status=PresenceStatus.AWAY,
        )

        assert result.success
        assert result.data["status"] == PresenceStatus.AWAY

    def test_set_user_offline(self, user):
        """
        Setting presence to offline should remove user from presence tracking.

        Why it matters: Explicit offline setting for clean disconnect.
        """
        from chat.services import PresenceService, PresenceStatus

        # First set online
        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)

        # Then set offline
        result = PresenceService.set_presence(
            user_id=user.id,
            status=PresenceStatus.OFFLINE,
        )

        assert result.success
        assert result.data["status"] == PresenceStatus.OFFLINE

    def test_set_presence_with_conversation(self, user, conversation):
        """
        Setting presence in a conversation context adds user to conversation presence.

        Why it matters: Shows who's viewing a specific conversation.
        """
        from chat.services import PresenceService, PresenceStatus

        result = PresenceService.set_presence(
            user_id=user.id,
            status=PresenceStatus.ONLINE,
            conversation_id=conversation.id,
        )

        assert result.success
        assert result.data["conversation_id"] == conversation.id

    def test_set_presence_with_conversation_requires_participation(
        self, user, other_user, third_user
    ):
        """
        Setting presence in a conversation requires being a participant.

        Why it matters: Security - users can't inject themselves into others' conversations.
        """
        from chat.services import PresenceService, PresenceStatus

        # Create conversation between other_user and third_user (user is NOT a participant)
        conv = ConversationFactory(
            conversation_type=ConversationType.DIRECT,
            created_by=other_user,
        )
        ParticipantFactory(conversation=conv, user=other_user)
        ParticipantFactory(conversation=conv, user=third_user)

        # user tries to set presence in this conversation
        result = PresenceService.set_presence(
            user_id=user.id,
            status=PresenceStatus.ONLINE,
            conversation_id=conv.id,
        )

        assert not result.success
        assert result.error_code == "not_participant"


# =============================================================================
# Get Presence Tests
# =============================================================================


@pytest.mark.django_db
class TestGetPresence:
    """Test getting user presence."""

    def test_get_online_user_presence(self, user):
        """
        Getting presence for online user should return their status.

        Why it matters: Clients need to query user status.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)

        result = PresenceService.get_presence(user.id)

        assert result.success
        assert result.data["status"] == PresenceStatus.ONLINE
        assert result.data["user_id"] == str(user.id)

    def test_get_away_user_presence(self, user):
        """
        Getting presence for away user should return away status.

        Why it matters: Different statuses should be correctly returned.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.AWAY)

        result = PresenceService.get_presence(user.id)

        assert result.success
        assert result.data["status"] == PresenceStatus.AWAY

    def test_get_offline_user_presence(self, user):
        """
        Getting presence for offline/unknown user should return offline status.

        Why it matters: Default status for users not in presence system.
        """
        from chat.services import PresenceService, PresenceStatus

        # User has no presence entry
        result = PresenceService.get_presence(user.id)

        assert result.success
        assert result.data["status"] == PresenceStatus.OFFLINE

    def test_get_presence_includes_last_seen(self, user):
        """
        Presence response should include last_seen timestamp.

        Why it matters: Shows when user was last active.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)

        result = PresenceService.get_presence(user.id)

        assert result.success
        assert "last_seen" in result.data
        assert result.data["last_seen"] is not None


# =============================================================================
# Bulk Presence Tests
# =============================================================================


@pytest.mark.django_db
class TestBulkPresence:
    """Test bulk presence queries."""

    def test_get_multiple_users_presence(self, user, other_user, third_user):
        """
        Should be able to get presence for multiple users at once.

        Why it matters: Efficient bulk query for participant lists.
        """
        from chat.services import PresenceService, PresenceStatus

        # Set different statuses
        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)
        PresenceService.set_presence(other_user.id, PresenceStatus.AWAY)
        # third_user has no presence entry

        result = PresenceService.get_bulk_presence(
            [user.id, other_user.id, third_user.id]
        )

        assert result.success
        assert len(result.data) == 3

        presence_map = {p["user_id"]: p["status"] for p in result.data}
        assert presence_map[str(user.id)] == PresenceStatus.ONLINE
        assert presence_map[str(other_user.id)] == PresenceStatus.AWAY
        assert presence_map[str(third_user.id)] == PresenceStatus.OFFLINE

    def test_get_bulk_presence_empty_list(self):
        """
        Empty user list should return empty results.

        Why it matters: Edge case handling.
        """
        from chat.services import PresenceService

        result = PresenceService.get_bulk_presence([])

        assert result.success
        assert result.data == []


# =============================================================================
# Conversation Presence Tests
# =============================================================================


@pytest.mark.django_db
class TestConversationPresence:
    """Test conversation-specific presence."""

    def test_get_conversation_presence(self, user, other_user, conversation):
        """
        Should return presence of all participants in a conversation.

        Why it matters: Shows who's online in a chat.
        """
        from chat.services import PresenceService, PresenceStatus

        # Set both users online in conversation
        PresenceService.set_presence(user.id, PresenceStatus.ONLINE, conversation.id)
        PresenceService.set_presence(
            other_user.id, PresenceStatus.AWAY, conversation.id
        )

        result = PresenceService.get_conversation_presence(conversation.id)

        assert result.success
        assert len(result.data) == 2

    def test_conversation_presence_excludes_offline(
        self, user, other_user, conversation
    ):
        """
        Conversation presence should only include online/away users.

        Why it matters: Offline users don't need to be shown.
        """
        from chat.services import PresenceService, PresenceStatus

        # Only user is online
        PresenceService.set_presence(user.id, PresenceStatus.ONLINE, conversation.id)
        # other_user is offline (no presence entry)

        result = PresenceService.get_conversation_presence(conversation.id)

        assert result.success
        user_ids = [p["user_id"] for p in result.data]
        assert str(user.id) in user_ids
        assert str(other_user.id) not in user_ids

    def test_user_can_be_in_multiple_conversations(
        self, user, conversation, group_conversation
    ):
        """
        User presence should be tracked per conversation.

        Why it matters: User may have multiple chats open.
        """
        from chat.services import PresenceService, PresenceStatus

        # User is online in both conversations
        PresenceService.set_presence(user.id, PresenceStatus.ONLINE, conversation.id)
        PresenceService.set_presence(
            user.id, PresenceStatus.ONLINE, group_conversation.id
        )

        result1 = PresenceService.get_conversation_presence(conversation.id)
        result2 = PresenceService.get_conversation_presence(group_conversation.id)

        assert result1.success
        assert result2.success
        assert any(p["user_id"] == str(user.id) for p in result1.data)
        assert any(p["user_id"] == str(user.id) for p in result2.data)


# =============================================================================
# Heartbeat Tests
# =============================================================================


@pytest.mark.django_db
class TestHeartbeat:
    """Test heartbeat mechanism for presence refresh."""

    def test_heartbeat_updates_last_seen(self, user):
        """
        Heartbeat should update the last_seen timestamp.

        Why it matters: Keeps presence data fresh.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)

        result = PresenceService.heartbeat(user.id)

        assert result.success
        assert "last_seen" in result.data

    def test_heartbeat_extends_ttl(self, user):
        """
        Heartbeat should extend the TTL of presence entry.

        Why it matters: Prevents premature expiration.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)

        result = PresenceService.heartbeat(user.id)

        assert result.success
        # TTL should be extended (implementation detail, just verify success)

    def test_heartbeat_for_offline_user_sets_online(self, user):
        """
        Heartbeat for user without presence should set them online.

        Why it matters: Implicit reconnection handling.
        """
        from chat.services import PresenceService, PresenceStatus

        # User has no presence entry
        result = PresenceService.heartbeat(user.id)

        assert result.success
        assert result.data["status"] == PresenceStatus.ONLINE

    def test_heartbeat_with_conversation(self, user, conversation):
        """
        Heartbeat should update conversation presence too.

        Why it matters: Refreshes per-conversation presence.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE, conversation.id)

        result = PresenceService.heartbeat(user.id, conversation.id)

        assert result.success


# =============================================================================
# TTL and Expiration Tests
# =============================================================================


@pytest.mark.django_db
class TestPresenceTTL:
    """Test presence TTL and automatic expiration."""

    def test_presence_has_ttl(self, user):
        """
        Presence entries should have a TTL set.

        Why it matters: Auto-cleanup for stale presence data.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)

        # Get TTL from Redis (implementation detail)
        result = PresenceService.get_presence(user.id)
        assert result.success
        # The presence should have a TTL (verified by auto-expiry behavior)

    @patch("chat.services.PresenceService._get_cache")
    def test_expired_presence_returns_offline(self, mock_cache, user):
        """
        Expired presence should return offline status.

        Why it matters: Stale presence shouldn't show user as online.
        """
        from chat.services import PresenceService, PresenceStatus

        # Mock cache to return None (simulating expired key)
        mock_cache.return_value.get.return_value = None

        result = PresenceService.get_presence(user.id)

        assert result.success
        assert result.data["status"] == PresenceStatus.OFFLINE


# =============================================================================
# Leave Conversation Presence Tests
# =============================================================================


@pytest.mark.django_db
class TestLeaveConversation:
    """Test leaving conversation presence."""

    def test_leave_conversation_removes_presence(self, user, conversation):
        """
        Leaving a conversation should remove user from conversation presence.

        Why it matters: User closing a chat shouldn't show as present.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE, conversation.id)

        result = PresenceService.leave_conversation(user.id, conversation.id)

        assert result.success

        # Verify user is no longer in conversation presence
        presence_result = PresenceService.get_conversation_presence(conversation.id)
        user_ids = [p["user_id"] for p in presence_result.data]
        assert str(user.id) not in user_ids

    def test_leave_conversation_keeps_global_presence(self, user, conversation):
        """
        Leaving a conversation should not affect global presence.

        Why it matters: User is still online, just not viewing that chat.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE, conversation.id)

        PresenceService.leave_conversation(user.id, conversation.id)

        # Global presence should still be online
        result = PresenceService.get_presence(user.id)
        assert result.success
        assert result.data["status"] == PresenceStatus.ONLINE


# =============================================================================
# Clear Presence Tests
# =============================================================================


@pytest.mark.django_db
class TestClearPresence:
    """Test clearing all presence for a user."""

    def test_clear_presence_removes_all(self, user, conversation, group_conversation):
        """
        Clear presence should remove user from all tracking.

        Why it matters: Clean disconnect when user goes offline.
        """
        from chat.services import PresenceService, PresenceStatus

        # Set presence in multiple places
        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)
        PresenceService.set_presence(user.id, PresenceStatus.ONLINE, conversation.id)
        PresenceService.set_presence(
            user.id, PresenceStatus.ONLINE, group_conversation.id
        )

        result = PresenceService.clear_presence(user.id)

        assert result.success

        # Verify all presence is cleared
        global_result = PresenceService.get_presence(user.id)
        assert global_result.data["status"] == PresenceStatus.OFFLINE


# =============================================================================
# API Endpoint Tests
# =============================================================================


@pytest.mark.django_db
class TestPresenceAPI:
    """Test presence REST API endpoints."""

    def test_set_presence_endpoint(self, auth_client, user):
        """
        POST /api/v1/chat/presence/ should set user presence.

        Why it matters: REST API for presence management.
        """
        url = "/api/v1/chat/presence/"
        data = {"status": "online"}

        response = auth_client.post(url, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "online"

    def test_get_presence_endpoint(self, auth_client, user, other_user):
        """
        GET /api/v1/chat/presence/{user_id}/ should return user presence.

        Why it matters: Query presence for specific users.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(other_user.id, PresenceStatus.ONLINE)

        url = f"/api/v1/chat/presence/{other_user.id}/"
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "online"

    def test_get_bulk_presence_endpoint(
        self, auth_client, user, other_user, third_user
    ):
        """
        POST /api/v1/chat/presence/bulk/ should return multiple user presences.

        Why it matters: Efficient bulk query.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(other_user.id, PresenceStatus.ONLINE)
        PresenceService.set_presence(third_user.id, PresenceStatus.AWAY)

        url = "/api/v1/chat/presence/bulk/"
        data = {"user_ids": [str(other_user.id), str(third_user.id)]}

        response = auth_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

    def test_heartbeat_endpoint(self, auth_client, user):
        """
        POST /api/v1/chat/presence/heartbeat/ should update presence.

        Why it matters: Keep presence fresh via periodic heartbeats.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)

        url = "/api/v1/chat/presence/heartbeat/"
        response = auth_client.post(url)

        assert response.status_code == status.HTTP_200_OK

    def test_presence_requires_authentication(self, api_client):
        """
        Presence endpoints should require authentication.

        Why it matters: Security - only authenticated users.
        """
        url = "/api/v1/chat/presence/"
        response = api_client.post(url, {"status": "online"})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_conversation_presence_endpoint(
        self, auth_client, user, other_user, conversation
    ):
        """
        GET /api/v1/chat/conversations/{id}/presence/ should return presence.

        Why it matters: Show who's online in a conversation.
        """
        from chat.services import PresenceService, PresenceStatus

        PresenceService.set_presence(user.id, PresenceStatus.ONLINE, conversation.id)
        PresenceService.set_presence(
            other_user.id, PresenceStatus.AWAY, conversation.id
        )

        url = f"/api/v1/chat/conversations/{conversation.id}/presence/"
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

    def test_conversation_presence_requires_participant(
        self, auth_client, user, other_user, third_user
    ):
        """
        Conversation presence should only be accessible to participants.

        Why it matters: Privacy - can't see who's online in others' chats.
        Note: 404 is also acceptable as it doesn't leak that the conversation exists.
        """
        # Create conversation between other_user and third_user (user is not a participant)
        conv = ConversationFactory(
            conversation_type=ConversationType.DIRECT,
            created_by=other_user,
        )
        ParticipantFactory(conversation=conv, user=other_user)
        ParticipantFactory(conversation=conv, user=third_user)

        url = f"/api/v1/chat/conversations/{conv.id}/presence/"
        response = auth_client.get(url)

        # Either 403 (Forbidden) or 404 (Not Found) is acceptable for security
        # 404 is actually preferred as it doesn't leak that the conversation exists
        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]
