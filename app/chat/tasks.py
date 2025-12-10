"""
Celery tasks for chat app.

This module defines async tasks for:
- Message notifications
- Conversation archival
- Message cleanup
- Read receipt syncing

Related files:
    - services.py: ChatService
    - models.py: Message, Conversation

Usage:
    from chat.tasks import send_message_notifications

    send_message_notifications.delay(message_id)
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_message_notifications(self, message_id: int) -> int:
    """
    Send notifications for new message.

    Notifies participants who are not currently connected
    via WebSocket.

    Args:
        message_id: ID of the message

    Returns:
        Number of notifications sent
    """
    # TODO: Implement
    # from .models import Message, ConversationParticipant
    # from notifications.services import NotificationService
    #
    # try:
    #     message = Message.objects.select_related(
    #         "conversation", "sender"
    #     ).get(id=message_id)
    # except Message.DoesNotExist:
    #     logger.error(f"Message {message_id} not found")
    #     return 0
    #
    # # Get participants to notify (excluding sender)
    # participants = ConversationParticipant.objects.filter(
    #     conversation=message.conversation,
    #     is_active=True,
    # ).exclude(user_id=message.sender_id).select_related("user")
    #
    # notified = 0
    # for participant in participants:
    #     # Skip if muted
    #     if participant.is_muted_now:
    #         continue
    #
    #     # TODO: Skip if connected via WebSocket
    #
    #     NotificationService.send(
    #         user=participant.user,
    #         notification_type="chat",
    #         title=f"Message from {message.sender.first_name or 'Someone'}",
    #         body=message.content_preview,
    #         channels=["push"],
    #         action_url=f"/chat/{message.conversation_id}",
    #         metadata={
    #             "conversation_id": message.conversation_id,
    #             "message_id": message.id,
    #         },
    #     )
    #     notified += 1
    #
    # logger.info(f"Sent {notified} notifications for message {message_id}")
    # return notified
    logger.info(f"send_message_notifications called for message {message_id} (not implemented)")
    return 0


@shared_task
def update_conversation_last_message(conversation_id: int) -> None:
    """
    Update conversation's last_message_at timestamp.

    Called after message creation to ensure consistent ordering.

    Args:
        conversation_id: ID of the conversation
    """
    # TODO: Implement
    # from django.db.models import Max
    # from .models import Conversation
    #
    # try:
    #     conversation = Conversation.objects.get(id=conversation_id)
    # except Conversation.DoesNotExist:
    #     return
    #
    # last_message = conversation.messages.aggregate(Max("created_at"))
    # conversation.last_message_at = last_message["created_at__max"]
    # conversation.save(update_fields=["last_message_at", "updated_at"])
    logger.info(
        f"update_conversation_last_message called for {conversation_id} (not implemented)"
    )


@shared_task
def archive_old_conversations(days: int = 365) -> int:
    """
    Archive inactive conversations.

    Archives conversations with no messages for specified days.

    Args:
        days: Archive conversations older than this

    Returns:
        Number of conversations archived
    """
    # TODO: Implement
    # from datetime import timedelta
    # from django.utils import timezone
    # from .models import Conversation
    #
    # cutoff = timezone.now() - timedelta(days=days)
    #
    # archived = Conversation.objects.filter(
    #     is_archived=False,
    #     last_message_at__lt=cutoff,
    # ).update(is_archived=True)
    #
    # logger.info(f"Archived {archived} old conversations")
    # return archived
    logger.info(f"archive_old_conversations called (days={days}) (not implemented)")
    return 0


@shared_task
def cleanup_deleted_messages(days: int = 30) -> int:
    """
    Hard delete soft-deleted messages.

    Permanently removes messages that have been soft-deleted
    for longer than specified days.

    Args:
        days: Delete messages soft-deleted before this

    Returns:
        Number of messages deleted
    """
    # TODO: Implement
    # from datetime import timedelta
    # from django.utils import timezone
    # from .models import Message
    #
    # cutoff = timezone.now() - timedelta(days=days)
    #
    # deleted, _ = Message.objects.filter(
    #     is_deleted=True,
    #     deleted_at__lt=cutoff,
    # ).delete()
    #
    # logger.info(f"Permanently deleted {deleted} messages")
    # return deleted
    logger.info(f"cleanup_deleted_messages called (days={days}) (not implemented)")
    return 0


@shared_task
def sync_read_receipts_bulk(
    conversation_id: int,
    user_id: int,
    message_ids: list[int],
) -> int:
    """
    Bulk sync read receipts.

    Creates read receipts for multiple messages at once.
    More efficient than individual creates.

    Args:
        conversation_id: Conversation ID
        user_id: User ID
        message_ids: List of message IDs to mark as read

    Returns:
        Number of receipts created
    """
    # TODO: Implement
    # from django.contrib.auth import get_user_model
    # from .models import Message, MessageReadReceipt
    #
    # User = get_user_model()
    # try:
    #     user = User.objects.get(id=user_id)
    # except User.DoesNotExist:
    #     return 0
    #
    # messages = Message.objects.filter(
    #     id__in=message_ids,
    #     conversation_id=conversation_id,
    # )
    #
    # receipts = [
    #     MessageReadReceipt(message=msg, user=user)
    #     for msg in messages
    # ]
    #
    # created = MessageReadReceipt.objects.bulk_create(
    #     receipts,
    #     ignore_conflicts=True,
    # )
    #
    # logger.info(f"Created {len(created)} read receipts for user {user_id}")
    # return len(created)
    logger.info(
        f"sync_read_receipts_bulk called for conversation {conversation_id} (not implemented)"
    )
    return 0
