"""
Integration tests for chat database migrations.

These tests verify PostgreSQL-specific features like:
- Search vector trigger functionality
- GIN index creation
- Proper trigger behavior for different message types

Note:
    These tests require a PostgreSQL database and run actual SQL.
    They are marked with @pytest.mark.integration.
"""

import pytest
from django.db import connection

from chat.models import Message, MessageType


@pytest.mark.integration
class TestSearchVectorTrigger:
    """Tests for the chat_message_search_vector_trigger PostgreSQL function."""

    def test_trigger_populates_search_vector_on_insert(
        self, db, group_conversation, owner_user
    ):
        """
        Search vector is populated automatically on INSERT for text messages.

        The trigger function should set search_vector to the tsvector of content
        when a new text message is created.
        """
        message = Message.objects.create(
            conversation=group_conversation,
            sender=owner_user,
            message_type=MessageType.TEXT,
            content="Hello world this is a test message",
        )

        # Refresh from database to get the trigger-updated value
        message.refresh_from_db()

        assert message.search_vector is not None
        # The search vector should contain the words from the content
        # We can verify by checking raw SQL
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT search_vector::text FROM chat_message WHERE id = %s",
                [message.id],
            )
            sv_text = cursor.fetchone()[0]
            # Should contain stemmed versions of words
            assert "hello" in sv_text or "world" in sv_text or "test" in sv_text

    def test_trigger_updates_search_vector_on_content_change(self, db, text_message):
        """
        Search vector is updated when content is modified.

        The trigger fires on UPDATE OF content, so changing the content
        should update the search_vector.
        """
        new_content = "completely different unique searchable content here"

        # Update using raw SQL to ensure trigger fires
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE chat_message SET content = %s WHERE id = %s",
                [new_content, text_message.id],
            )

        text_message.refresh_from_db()

        # Verify the search vector was updated
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT search_vector::text FROM chat_message WHERE id = %s",
                [text_message.id],
            )
            sv_text = cursor.fetchone()[0]
            # Should contain new content words, not old ones
            assert "unique" in sv_text or "searchabl" in sv_text

    def test_trigger_sets_null_for_system_messages(self, db, group_conversation):
        """
        System messages should have search_vector = NULL.

        The trigger function checks message_type and only populates
        search_vector for 'text' messages.
        """
        message = Message.objects.create(
            conversation=group_conversation,
            sender=None,  # System messages have no sender
            message_type=MessageType.SYSTEM,
            content='{"event": "user_joined", "data": {"user_id": 123}}',
        )

        message.refresh_from_db()

        assert message.search_vector is None

    def test_trigger_clears_search_vector_when_type_changes_to_system(
        self, db, text_message
    ):
        """
        If message_type changes from text to system, search_vector should be NULL.

        Note: This is an edge case - message types shouldn't normally change.
        But the trigger should handle it correctly.
        """
        # First verify text message has a search_vector
        text_message.refresh_from_db()
        assert text_message.search_vector is not None

        # Change type to system using raw SQL
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE chat_message SET message_type = %s WHERE id = %s",
                [MessageType.SYSTEM, text_message.id],
            )

        text_message.refresh_from_db()
        assert text_message.search_vector is None

    def test_search_vector_enables_full_text_search(
        self, db, group_conversation, owner_user
    ):
        """
        Verify that the search vector can be used for full-text search queries.

        This tests the end-to-end functionality of the search infrastructure.
        """
        # Create messages with different content
        Message.objects.create(
            conversation=group_conversation,
            sender=owner_user,
            message_type=MessageType.TEXT,
            content="The quick brown fox jumps over the lazy dog",
        )
        Message.objects.create(
            conversation=group_conversation,
            sender=owner_user,
            message_type=MessageType.TEXT,
            content="Python is a great programming language",
        )
        Message.objects.create(
            conversation=group_conversation,
            sender=owner_user,
            message_type=MessageType.TEXT,
            content="Django makes web development fun",
        )

        # Search for "programming" using to_tsquery
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, content FROM chat_message
                WHERE search_vector @@ to_tsquery('english', 'programming')
            """)
            results = cursor.fetchall()

        assert len(results) == 1
        assert "programming" in results[0][1]

    def test_search_vector_handles_empty_content(
        self, db, group_conversation, owner_user
    ):
        """
        Messages with empty content should get an empty search vector, not NULL.
        """
        message = Message.objects.create(
            conversation=group_conversation,
            sender=owner_user,
            message_type=MessageType.TEXT,
            content="",
        )

        message.refresh_from_db()

        # Empty content should still produce a search vector (empty tsvector)
        # The COALESCE in the trigger ensures this
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT search_vector FROM chat_message WHERE id = %s",
                [message.id],
            )
            result = cursor.fetchone()[0]
            # Empty tsvector is still not NULL
            assert result is not None or result == ""


@pytest.mark.integration
class TestGinIndexExists:
    """Tests to verify the GIN index was created correctly."""

    def test_search_vector_gin_index_exists(self, db):
        """
        Verify that the GIN index on search_vector was created.

        The index should be named 'chat_msg_search_vector_idx'.
        """
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'chat_message'
                AND indexname = 'chat_msg_search_vector_idx'
            """)
            result = cursor.fetchone()

        assert result is not None, "GIN index 'chat_msg_search_vector_idx' not found"
        index_name, index_def = result
        assert "gin" in index_def.lower(), f"Index is not a GIN index: {index_def}"
        assert "search_vector" in index_def.lower()

    def test_trigger_function_exists(self, db):
        """
        Verify that the trigger function was created.
        """
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT proname
                FROM pg_proc
                WHERE proname = 'chat_message_search_vector_trigger'
            """)
            result = cursor.fetchone()

        assert result is not None, (
            "Trigger function 'chat_message_search_vector_trigger' not found"
        )

    def test_trigger_exists_on_table(self, db):
        """
        Verify that the trigger is attached to the chat_message table.
        """
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT tgname
                FROM pg_trigger
                WHERE tgname = 'chat_message_search_vector_update'
                AND tgrelid = 'chat_message'::regclass
            """)
            result = cursor.fetchone()

        assert result is not None, (
            "Trigger 'chat_message_search_vector_update' not found on chat_message table"
        )
