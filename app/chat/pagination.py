"""
Pagination classes for chat API.

This module provides cursor-based pagination for the chat system:
- MessageCursorPagination: For message lists (oldest first by default)
- ConversationCursorPagination: For conversation lists (most recent first)

Cursor-based pagination advantages:
- Stable results during concurrent inserts
- Efficient for large datasets
- No offset calculation needed

Design Decisions:
    - Messages ordered oldest-first for natural reading flow
    - Conversations ordered by most recent activity
    - Cursors encode (created_at, id) for stability
    - Page sizes balanced for mobile performance
"""

from rest_framework.pagination import CursorPagination


class MessageCursorPagination(CursorPagination):
    """
    Cursor pagination for message lists.

    Orders messages oldest-first for natural chat reading experience.
    Uses (created_at, id) for stable cursor position.

    Default: 50 messages per page
    Maximum: 100 messages per page

    Query parameters:
        cursor: Encoded cursor for position
        page_size: Number of messages (optional override)
    """

    page_size = 50
    max_page_size = 100
    page_size_query_param = "page_size"
    ordering = ("created_at", "id")
    cursor_query_param = "cursor"


class ConversationCursorPagination(CursorPagination):
    """
    Cursor pagination for conversation lists.

    Orders conversations by most recent activity (last_message_at, then created_at).
    Uses descending order so most active conversations appear first.

    Default: 20 conversations per page
    Maximum: 50 conversations per page

    Query parameters:
        cursor: Encoded cursor for position
        page_size: Number of conversations (optional override)
    """

    page_size = 20
    max_page_size = 50
    page_size_query_param = "page_size"
    ordering = ("-last_message_at", "-created_at", "-id")
    cursor_query_param = "cursor"
