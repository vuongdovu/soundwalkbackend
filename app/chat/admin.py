"""
Django admin configuration for chat app.

Registers:
    - Conversation: Chat conversations
    - ConversationParticipant: Participation records
    - Message: Chat messages

Usage:
    Access via /admin/chat/
"""


# TODO: Uncomment when models are implemented
# from .models import Conversation, ConversationParticipant, Message


# TODO: Implement admin classes
# class ConversationParticipantInline(admin.TabularInline):
#     """Inline admin for conversation participants."""
#     model = ConversationParticipant
#     extra = 0
#     readonly_fields = ["last_read_at", "left_at"]
#     raw_id_fields = ["user"]


# @admin.register(Conversation)
# class ConversationAdmin(admin.ModelAdmin):
#     """Admin for Conversation model."""
#
#     list_display = [
#         "id",
#         "conversation_type",
#         "name",
#         "participant_count",
#         "message_count",
#         "last_message_at",
#         "is_archived",
#     ]
#     list_filter = ["conversation_type", "is_archived"]
#     search_fields = ["name", "participants__email"]
#     readonly_fields = ["created_at", "updated_at", "last_message_at"]
#     inlines = [ConversationParticipantInline]
#
#     fieldsets = (
#         (None, {
#             "fields": ("conversation_type", "name", "is_archived"),
#         }),
#         ("Metadata", {
#             "fields": ("metadata",),
#             "classes": ("collapse",),
#         }),
#         ("Timestamps", {
#             "fields": ("last_message_at", "created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )
#
#     def participant_count(self, obj):
#         return obj.participants.count()
#     participant_count.short_description = "Participants"
#
#     def message_count(self, obj):
#         return obj.messages.count()
#     message_count.short_description = "Messages"


# @admin.register(Message)
# class MessageAdmin(admin.ModelAdmin):
#     """Admin for Message model."""
#
#     list_display = [
#         "id",
#         "conversation",
#         "sender",
#         "message_type",
#         "content_preview",
#         "is_edited",
#         "is_deleted",
#         "created_at",
#     ]
#     list_filter = ["message_type", "is_edited", "is_deleted"]
#     search_fields = ["content", "sender__email"]
#     readonly_fields = ["created_at", "updated_at", "edited_at", "deleted_at"]
#     raw_id_fields = ["conversation", "sender", "reply_to"]
#     date_hierarchy = "created_at"
#
#     fieldsets = (
#         (None, {
#             "fields": ("conversation", "sender", "message_type"),
#         }),
#         ("Content", {
#             "fields": ("content", "reply_to"),
#         }),
#         ("Status", {
#             "fields": ("is_edited", "edited_at", "is_deleted", "deleted_at"),
#         }),
#         ("Metadata", {
#             "fields": ("metadata",),
#             "classes": ("collapse",),
#         }),
#         ("Timestamps", {
#             "fields": ("created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )
#
#     def content_preview(self, obj):
#         if obj.is_deleted:
#             return "[Deleted]"
#         return obj.content[:50] + "..." if len(obj.content) > 50 else obj.content
#     content_preview.short_description = "Content"
