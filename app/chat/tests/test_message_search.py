"""
Tests for message search feature.

This module tests the MessageSearchService which handles:
- Full-text search across user's accessible messages
- Keyset pagination with cursor-based navigation
- Conversation filtering
- Authorization (only search messages user can access)

The search uses PostgreSQL's full-text search capabilities with:
- Automatic search_vector updates via database trigger
- English text configuration for stemming and stop words
- Ranked results by relevance

TDD approach: These tests are written first to drive implementation.
"""

import base64
import pytest

from authentication.tests.factories import UserFactory
from chat.models import (
    ConversationType,
    Message,
    MessageType,
    ParticipantRole,
)
from chat.services import MessageSearchService
from chat.tests.factories import (
    ConversationFactory,
    ParticipantFactory,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def user(db):
    """User who will search messages."""
    return UserFactory()


@pytest.fixture
def other_user(db):
    """Another user for testing."""
    return UserFactory()


@pytest.fixture
def conversation1(db, user):
    """First conversation with user as participant."""
    conversation = ConversationFactory(
        conversation_type=ConversationType.GROUP,
        title="Project Alpha",
    )
    ParticipantFactory(
        conversation=conversation,
        user=user,
        role=ParticipantRole.OWNER,
    )
    return conversation


@pytest.fixture
def conversation2(db, user):
    """Second conversation with user as participant."""
    conversation = ConversationFactory(
        conversation_type=ConversationType.GROUP,
        title="Team Beta",
    )
    ParticipantFactory(
        conversation=conversation,
        user=user,
        role=ParticipantRole.MEMBER,
    )
    return conversation


@pytest.fixture
def inaccessible_conversation(db, other_user):
    """Conversation the user is NOT a participant in."""
    conversation = ConversationFactory(
        conversation_type=ConversationType.GROUP,
        title="Secret Group",
    )
    ParticipantFactory(
        conversation=conversation,
        user=other_user,
        role=ParticipantRole.OWNER,
    )
    return conversation


@pytest.fixture
def searchable_messages(db, conversation1, conversation2, user, other_user):
    """
    Create searchable messages across multiple conversations.

    Messages include specific words for testing search.
    """
    # Add other_user to conversation1 for some messages
    ParticipantFactory(
        conversation=conversation1,
        user=other_user,
        role=ParticipantRole.MEMBER,
    )

    messages = []

    # Messages in conversation1
    messages.append(
        Message.objects.create(
            conversation=conversation1,
            sender=user,
            message_type=MessageType.TEXT,
            content="The quick brown fox jumps over the lazy dog",
        )
    )
    messages.append(
        Message.objects.create(
            conversation=conversation1,
            sender=other_user,
            message_type=MessageType.TEXT,
            content="Django is a powerful web framework for Python",
        )
    )
    messages.append(
        Message.objects.create(
            conversation=conversation1,
            sender=user,
            message_type=MessageType.TEXT,
            content="Meeting tomorrow at 3pm to discuss the project",
        )
    )

    # Messages in conversation2
    messages.append(
        Message.objects.create(
            conversation=conversation2,
            sender=user,
            message_type=MessageType.TEXT,
            content="Python programming is fun and powerful",
        )
    )
    messages.append(
        Message.objects.create(
            conversation=conversation2,
            sender=user,
            message_type=MessageType.TEXT,
            content="Let's schedule a meeting next week",
        )
    )

    return messages


@pytest.fixture
def inaccessible_message(db, inaccessible_conversation, other_user):
    """Message the user cannot access."""
    return Message.objects.create(
        conversation=inaccessible_conversation,
        sender=other_user,
        message_type=MessageType.TEXT,
        content="This is a secret message about Python and meetings",
    )


# =============================================================================
# TestMessageSearchService - Basic Search
# =============================================================================


@pytest.mark.django_db
class TestBasicSearch:
    """Tests for basic search functionality."""

    def test_search_finds_exact_match(self, user, searchable_messages):
        """
        Search finds messages with exact word match.

        Why it matters: Core search functionality.
        """
        result = MessageSearchService.search(
            user=user,
            query="Django",
        )

        assert result.success is True
        assert len(result.data["results"]) >= 1
        # Verify the Django message is found
        content_list = [m.content for m in result.data["results"]]
        assert any("Django" in c for c in content_list)

    def test_search_finds_partial_match_via_stemming(self, user, searchable_messages):
        """
        Search uses stemming to find related words.

        Why it matters: PostgreSQL FTS uses stemming for better matches.
        """
        # Search for "programming" should also find "program" variants
        result = MessageSearchService.search(
            user=user,
            query="programming",
        )

        assert result.success is True
        # Should find "Python programming" message
        content_list = [m.content for m in result.data["results"]]
        assert any("programming" in c.lower() for c in content_list)

    def test_search_is_case_insensitive(self, user, searchable_messages):
        """
        Search is case insensitive.

        Why it matters: User experience - shouldn't need to match case.
        """
        result = MessageSearchService.search(
            user=user,
            query="PYTHON",
        )

        assert result.success is True
        assert len(result.data["results"]) >= 1

    def test_search_empty_query_returns_error(self, user):
        """
        Empty query string returns error.

        Why it matters: Prevent wasteful empty searches.
        """
        result = MessageSearchService.search(
            user=user,
            query="",
        )

        assert result.success is False
        assert result.error_code == "INVALID_QUERY"

    def test_search_short_query_returns_error(self, user):
        """
        Query shorter than minimum length returns error.

        Why it matters: Prevent overly broad searches.
        """
        result = MessageSearchService.search(
            user=user,
            query="a",  # Too short
        )

        assert result.success is False
        assert result.error_code == "QUERY_TOO_SHORT"

    def test_search_no_results(self, user, searchable_messages):
        """
        Search with no matches returns empty results.

        Why it matters: Proper handling of no matches.
        """
        result = MessageSearchService.search(
            user=user,
            query="xyznonexistent123",
        )

        assert result.success is True
        assert result.data["results"] == []
        assert result.data["has_more"] is False

    def test_search_handles_special_characters(self, user, conversation1):
        """
        Search handles special characters safely.

        Why it matters: Security and stability with user input.
        """
        # Create a message with special characters
        Message.objects.create(
            conversation=conversation1,
            sender=user,
            message_type=MessageType.TEXT,
            content="Testing special chars: @user #hashtag $100",
        )

        # Should not error
        result = MessageSearchService.search(
            user=user,
            query="@user",
        )

        assert result.success is True  # Should not error


# =============================================================================
# TestMessageSearchService - Authorization
# =============================================================================


@pytest.mark.django_db
class TestSearchAuthorization:
    """Tests for search authorization and filtering."""

    def test_search_only_returns_accessible_messages(
        self, user, searchable_messages, inaccessible_message
    ):
        """
        Search only returns messages from conversations user participates in.

        Why it matters: Privacy - users shouldn't see others' messages.
        """
        # inaccessible_message contains "Python"
        result = MessageSearchService.search(
            user=user,
            query="Python",
        )

        assert result.success is True
        # Should find Python messages from accessible conversations
        assert len(result.data["results"]) >= 1

        # Should NOT include the inaccessible message
        message_ids = [m.id for m in result.data["results"]]
        assert inaccessible_message.id not in message_ids

    def test_search_excludes_deleted_messages(self, user, conversation1):
        """
        Search excludes soft-deleted messages.

        Why it matters: Deleted messages shouldn't appear in search.
        """
        message = Message.objects.create(
            conversation=conversation1,
            sender=user,
            message_type=MessageType.TEXT,
            content="This message will be deleted searchable",
        )

        # Verify it's found before deletion
        result = MessageSearchService.search(user=user, query="searchable")
        assert any(m.id == message.id for m in result.data["results"])

        # Delete the message
        message.soft_delete()

        # Should not be found after deletion
        result = MessageSearchService.search(user=user, query="searchable")
        assert not any(m.id == message.id for m in result.data["results"])


# =============================================================================
# TestMessageSearchService - Conversation Filtering
# =============================================================================


@pytest.mark.django_db
class TestConversationFiltering:
    """Tests for filtering search by conversation."""

    def test_search_respects_conversation_filter(
        self, user, conversation1, conversation2, searchable_messages
    ):
        """
        Search can be filtered to a specific conversation.

        Why it matters: Users often want to search within a conversation.
        """
        # Both conversations have "meeting" messages
        result = MessageSearchService.search(
            user=user,
            query="meeting",
            conversation_id=conversation1.id,
        )

        assert result.success is True
        # Should only find messages from conversation1
        for message in result.data["results"]:
            assert message.conversation_id == conversation1.id

    def test_search_with_invalid_conversation_returns_error(self, user):
        """
        Search with non-existent conversation ID returns error.

        Why it matters: Clear error handling for invalid input.
        """
        result = MessageSearchService.search(
            user=user,
            query="test",
            conversation_id=99999,
        )

        assert result.success is False
        assert result.error_code == "CONVERSATION_NOT_FOUND"

    def test_search_with_inaccessible_conversation_returns_error(
        self, user, inaccessible_conversation
    ):
        """
        Search in conversation user isn't part of returns error.

        Why it matters: Authorization at search level.
        """
        result = MessageSearchService.search(
            user=user,
            query="test",
            conversation_id=inaccessible_conversation.id,
        )

        assert result.success is False
        assert result.error_code == "CONVERSATION_NOT_ACCESSIBLE"


# =============================================================================
# TestMessageSearchService - Pagination
# =============================================================================


@pytest.mark.django_db
class TestSearchPagination:
    """Tests for keyset pagination in search results."""

    @pytest.fixture
    def many_messages(self, conversation1, user):
        """Create many messages for pagination testing."""
        messages = []
        for i in range(25):
            messages.append(
                Message.objects.create(
                    conversation=conversation1,
                    sender=user,
                    message_type=MessageType.TEXT,
                    content=f"Pagination test message number {i} with keyword searchterm",
                )
            )
        return messages

    def test_search_pagination_first_page(self, user, many_messages):
        """
        First page returns results with cursor for next page.

        Why it matters: Proper pagination setup.
        """
        result = MessageSearchService.search(
            user=user,
            query="searchterm",
            page_size=10,
        )

        assert result.success is True
        assert len(result.data["results"]) == 10
        assert result.data["has_more"] is True
        assert result.data["next_cursor"] is not None

    def test_search_pagination_with_cursor(self, user, many_messages):
        """
        Cursor navigates to next page correctly.

        Why it matters: Core pagination functionality.
        """
        # Get first page
        result1 = MessageSearchService.search(
            user=user,
            query="searchterm",
            page_size=10,
        )

        first_page_ids = [m.id for m in result1.data["results"]]

        # Get second page using cursor
        result2 = MessageSearchService.search(
            user=user,
            query="searchterm",
            page_size=10,
            cursor=result1.data["next_cursor"],
        )

        assert result2.success is True
        second_page_ids = [m.id for m in result2.data["results"]]

        # Pages should have no overlap
        assert not set(first_page_ids).intersection(set(second_page_ids))

    def test_search_pagination_last_page(self, user, many_messages):
        """
        Last page returns has_more=False and no cursor.

        Why it matters: Clear indication when results are exhausted.
        """
        # Fetch pages until we reach the end
        cursor = None
        pages = []

        while True:
            result = MessageSearchService.search(
                user=user,
                query="searchterm",
                page_size=10,
                cursor=cursor,
            )
            pages.append(result.data["results"])

            if not result.data["has_more"]:
                break

            cursor = result.data["next_cursor"]

        # Last page should have has_more=False
        assert not result.data["has_more"]
        # Should have no next_cursor or it's None
        assert result.data.get("next_cursor") is None

    def test_search_pagination_cursor_encoding(self, user, many_messages):
        """
        Cursor is properly encoded and decoded.

        Why it matters: Cursor should be opaque to client.
        """
        result = MessageSearchService.search(
            user=user,
            query="searchterm",
            page_size=5,
        )

        cursor = result.data["next_cursor"]

        # Cursor should be base64 encoded
        assert cursor is not None
        # Should be able to decode it (basic validation)
        try:
            decoded = base64.b64decode(cursor)
            # Should contain some valid data
            assert len(decoded) > 0
        except Exception:
            pytest.fail("Cursor should be valid base64")

    def test_search_pagination_invalid_cursor(self, user, many_messages):
        """
        Invalid cursor returns error.

        Why it matters: Proper error handling for bad input.
        """
        result = MessageSearchService.search(
            user=user,
            query="searchterm",
            cursor="invalid_cursor_value",
        )

        assert result.success is False
        assert result.error_code == "INVALID_CURSOR"


# =============================================================================
# TestSearchAPI
# =============================================================================


@pytest.fixture
def api_client(user):
    """API client authenticated as user."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def unauthenticated_client():
    """API client without authentication."""
    from rest_framework.test import APIClient

    return APIClient()


@pytest.mark.django_db
class TestSearchAPI:
    """
    Tests for message search API endpoint.

    Endpoint tested:
    - GET /api/v1/chat/messages/search/?q=query&conversation_id=X&cursor=Y
    """

    def test_search_endpoint_basic(self, api_client, searchable_messages):
        """
        GET request successfully searches messages.

        Why it matters: Primary API for search.
        """
        response = api_client.get(
            "/api/v1/chat/messages/search/",
            {"q": "Python"},
        )

        assert response.status_code == 200
        assert "results" in response.data
        assert "has_more" in response.data

    def test_search_endpoint_with_conversation_filter(
        self, api_client, searchable_messages, conversation1
    ):
        """
        Search can be filtered by conversation_id.

        Why it matters: Conversation-scoped search.
        """
        response = api_client.get(
            "/api/v1/chat/messages/search/",
            {"q": "meeting", "conversation_id": conversation1.id},
        )

        assert response.status_code == 200
        # All results should be from conversation1
        for result in response.data["results"]:
            assert result["conversation_id"] == conversation1.id

    def test_search_endpoint_pagination(self, api_client, user, conversation1):
        """
        Search pagination works via cursor parameter.

        Why it matters: API pagination.
        """
        # Create many messages
        for i in range(15):
            Message.objects.create(
                conversation=conversation1,
                sender=user,
                message_type=MessageType.TEXT,
                content=f"API test message {i} with term searchapi",
            )

        # First page
        response1 = api_client.get(
            "/api/v1/chat/messages/search/",
            {"q": "searchapi", "page_size": 5},
        )

        assert response1.status_code == 200
        assert len(response1.data["results"]) == 5
        assert response1.data["next_cursor"] is not None

        # Second page with cursor
        response2 = api_client.get(
            "/api/v1/chat/messages/search/",
            {"q": "searchapi", "page_size": 5, "cursor": response1.data["next_cursor"]},
        )

        assert response2.status_code == 200
        assert len(response2.data["results"]) == 5

    def test_search_requires_authentication(
        self, unauthenticated_client, searchable_messages
    ):
        """
        Search requires authenticated user.

        Why it matters: Security.
        """
        response = unauthenticated_client.get(
            "/api/v1/chat/messages/search/",
            {"q": "test"},
        )

        assert response.status_code == 401

    def test_search_missing_query_param(self, api_client):
        """
        Search without query parameter returns error.

        Why it matters: Required parameter validation.
        """
        response = api_client.get("/api/v1/chat/messages/search/")

        assert response.status_code == 400
