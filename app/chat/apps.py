"""
Chat application configuration.

This app provides the chat system with:
- Direct (1:1) and group conversations
- Role-based permissions (owner, admin, member)
- Message threading and soft deletion
- Read tracking and unread counts
"""

from django.apps import AppConfig


class ChatConfig(AppConfig):
    """Configuration for the chat application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"
    verbose_name = "Chat"
