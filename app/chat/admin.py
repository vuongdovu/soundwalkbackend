"""
Django admin configuration for chat models.

Provides admin interfaces for:
- Conversation management
- Participant viewing
- Message moderation
"""

from django.contrib import admin

from chat.models import Conversation, DirectConversationPair, Message, Participant


class ParticipantInline(admin.TabularInline):
    """Inline display of participants in conversation admin."""

    model = Participant
    extra = 0
    readonly_fields = [
        "joined_at",
        "left_at",
        "left_voluntarily",
        "removed_by",
        "last_read_at",
    ]
    raw_id_fields = ["user", "removed_by"]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    """Admin interface for Conversation model."""

    list_display = [
        "id",
        "conversation_type",
        "title",
        "participant_count",
        "is_deleted",
        "created_at",
        "last_message_at",
    ]
    list_filter = ["conversation_type", "is_deleted", "created_at"]
    search_fields = ["title", "id"]
    readonly_fields = [
        "created_at",
        "updated_at",
        "deleted_at",
        "participant_count",
        "last_message_at",
    ]
    raw_id_fields = ["created_by"]
    inlines = [ParticipantInline]
    ordering = ["-created_at"]


@admin.register(DirectConversationPair)
class DirectConversationPairAdmin(admin.ModelAdmin):
    """Admin interface for DirectConversationPair model."""

    list_display = ["conversation", "user_lower", "user_higher"]
    raw_id_fields = ["conversation", "user_lower", "user_higher"]


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    """Admin interface for Participant model."""

    list_display = [
        "id",
        "conversation",
        "user",
        "role",
        "joined_at",
        "left_at",
        "left_voluntarily",
    ]
    list_filter = ["role", "left_voluntarily", "joined_at"]
    search_fields = ["user__email", "conversation__title"]
    readonly_fields = ["created_at", "updated_at", "joined_at"]
    raw_id_fields = ["conversation", "user", "removed_by"]
    ordering = ["-joined_at"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    """Admin interface for Message model."""

    list_display = [
        "id",
        "conversation",
        "sender",
        "message_type",
        "content_preview",
        "is_deleted",
        "created_at",
    ]
    list_filter = ["message_type", "is_deleted", "created_at"]
    search_fields = ["content", "sender__email"]
    readonly_fields = ["created_at", "updated_at", "deleted_at", "reply_count"]
    raw_id_fields = ["conversation", "sender", "parent_message"]
    ordering = ["-created_at"]

    @admin.display(description="Content Preview")
    def content_preview(self, obj: Message) -> str:
        """Return truncated content for list display."""
        max_length = 50
        if len(obj.content) > max_length:
            return obj.content[:max_length] + "..."
        return obj.content
