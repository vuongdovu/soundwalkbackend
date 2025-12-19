"""
Service-level authorization for chat operations.

This module provides centralized authorization checks for chat features.
It is distinct from DRF permission classes (in permissions.py) which handle
HTTP-level authorization.

Key Components:
    ChatAuthorizationService: Stateless service class with authorization methods
    require_conversation_participant: Decorator for conversation-level access
    require_message_access: Decorator for message-level access with injection

Error Codes:
    NOT_PARTICIPANT: User is not an active participant in the conversation
    MESSAGE_NOT_FOUND: Message does not exist or is soft-deleted
    INVALID_REQUEST: Missing required parameters (user or ID)

Usage:
    # Direct method call
    if ChatAuthorizationService.is_conversation_participant(user, conversation_id):
        # proceed with operation

    # Decorator usage
    class MessageService(BaseService):
        @classmethod
        @require_message_access()
        def edit_message(cls, user, message_id, content, _message=None):
            # _message is injected by decorator
            _message.content = content
            _message.save()
            return ServiceResult.success(_message)
"""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Callable, Optional, Tuple, TypeVar

from core.services import ServiceResult

if TYPE_CHECKING:
    from authentication.models import User
    from chat.models import Message


T = TypeVar("T")


class ChatAuthorizationService:
    """
    Stateless service providing authorization checks for chat operations.

    All methods are classmethods and can be called directly without instantiation.
    Methods return simple boolean or tuple values for easy composition.

    Performance Notes:
        - Methods use optimized queries with select_related where appropriate
        - Results are not cached; callers should cache if needed
        - Bulk operations (get_user_conversation_ids) return lists for efficiency
    """

    @classmethod
    def is_conversation_participant(
        cls,
        user: "User",
        conversation_id: int,
    ) -> bool:
        """
        Check if user is an active participant in the conversation.

        Active means: Participant record exists with left_at=NULL.
        Does not check conversation soft-delete status.

        Args:
            user: User to check
            conversation_id: ID of the conversation

        Returns:
            True if user is active participant, False otherwise
        """
        from chat.models import Participant

        return Participant.objects.filter(
            conversation_id=conversation_id,
            user=user,
            left_at__isnull=True,
        ).exists()

    @classmethod
    def is_message_author(
        cls,
        user: "User",
        message_id: int,
    ) -> bool:
        """
        Check if user is the author of the message.

        System messages (sender=NULL) return False for any user.
        Does not check message soft-delete status.

        Args:
            user: User to check
            message_id: ID of the message

        Returns:
            True if user is the message sender, False otherwise
        """
        from chat.models import Message

        return Message.objects.filter(
            id=message_id,
            sender=user,
        ).exists()

    @classmethod
    def can_access_message(
        cls,
        user: "User",
        message_id: int,
    ) -> Tuple[bool, Optional["Message"]]:
        """
        Check if user can access a message and return the message if so.

        Access requires:
            1. Message exists and is not soft-deleted
            2. User is active participant in the message's conversation

        Args:
            user: User requesting access
            message_id: ID of the message

        Returns:
            Tuple of (can_access: bool, message: Message or None)
            - (True, message) if access granted
            - (False, None) if message not found, deleted, or unauthorized
        """
        from chat.models import Message

        try:
            message = Message.objects.select_related("conversation").get(
                id=message_id,
                is_deleted=False,
            )
        except Message.DoesNotExist:
            return False, None

        if not cls.is_conversation_participant(user, message.conversation_id):
            return False, None

        return True, message

    @classmethod
    def get_user_conversation_ids(cls, user: "User") -> list[int]:
        """
        Get IDs of all conversations where user is an active participant.

        Returns only conversations where user has not left (left_at=NULL).
        Includes both direct and group conversations.

        Args:
            user: User to get conversations for

        Returns:
            List of conversation IDs (empty list if no participations)
        """
        from chat.models import Participant

        return list(
            Participant.objects.filter(
                user=user,
                left_at__isnull=True,
            ).values_list("conversation_id", flat=True)
        )

    @classmethod
    def get_participant_role(
        cls,
        user: "User",
        conversation_id: int,
    ) -> Optional[str]:
        """
        Get user's role in a conversation.

        Args:
            user: User to get role for
            conversation_id: ID of the conversation

        Returns:
            Role string ('owner', 'admin', 'member') for group conversations
            None for direct conversations (participants have no role)
            None if user is not an active participant
        """
        from chat.models import Participant

        participant = Participant.objects.filter(
            conversation_id=conversation_id,
            user=user,
            left_at__isnull=True,
        ).first()

        if participant is None:
            return None

        return participant.role


def require_conversation_participant(
    conversation_id_param: str = "conversation_id",
    user_param: str = "user",
) -> Callable:
    """
    Decorator that requires user to be an active conversation participant.

    Extracts user and conversation_id from method kwargs and checks
    participation before allowing the method to execute.

    Args:
        conversation_id_param: Name of the kwarg containing conversation ID
        user_param: Name of the kwarg containing user (default: "user")

    Returns:
        ServiceResult.failure with NOT_PARTICIPANT if check fails
        ServiceResult.failure with INVALID_REQUEST if required params missing

    Example:
        class ConversationService(BaseService):
            @classmethod
            @require_conversation_participant()
            def send_message(cls, user, conversation_id, content):
                # Only called if user is participant
                ...

            @classmethod
            @require_conversation_participant(conversation_id_param="conv_id")
            def custom_method(cls, user, conv_id):
                ...
    """

    def decorator(
        func: Callable[..., ServiceResult[T]],
    ) -> Callable[..., ServiceResult[T]]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> ServiceResult[T]:
            user = kwargs.get(user_param)
            conversation_id = kwargs.get(conversation_id_param)

            if user is None or conversation_id is None:
                return ServiceResult.failure(
                    "Missing required parameters",
                    error_code="INVALID_REQUEST",
                )

            if not ChatAuthorizationService.is_conversation_participant(
                user, conversation_id
            ):
                return ServiceResult.failure(
                    "User is not a participant in this conversation",
                    error_code="NOT_PARTICIPANT",
                )

            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_message_access(
    message_id_param: str = "message_id",
    user_param: str = "user",
) -> Callable:
    """
    Decorator that requires user to have access to a message.

    Checks that message exists, is not deleted, and user is a participant
    in the message's conversation. On success, injects the message object
    as _message kwarg to avoid redundant database queries.

    Args:
        message_id_param: Name of the kwarg containing message ID
        user_param: Name of the kwarg containing user (default: "user")

    Returns:
        ServiceResult.failure with MESSAGE_NOT_FOUND if message doesn't exist/deleted
        ServiceResult.failure with NOT_PARTICIPANT if user not in conversation
        ServiceResult.failure with INVALID_REQUEST if required params missing

    Example:
        class MessageService(BaseService):
            @classmethod
            @require_message_access()
            def edit_message(cls, user, message_id, content, _message=None):
                # _message is injected and ready to use
                _message.content = content
                _message.save()
                return ServiceResult.success(_message)
    """

    def decorator(
        func: Callable[..., ServiceResult[T]],
    ) -> Callable[..., ServiceResult[T]]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> ServiceResult[T]:
            user = kwargs.get(user_param)
            message_id = kwargs.get(message_id_param)

            if user is None or message_id is None:
                return ServiceResult.failure(
                    "Missing required parameters",
                    error_code="INVALID_REQUEST",
                )

            # First check if message exists and is not deleted
            from chat.models import Message

            try:
                message = Message.objects.select_related("conversation").get(
                    id=message_id,
                    is_deleted=False,
                )
            except Message.DoesNotExist:
                return ServiceResult.failure(
                    "Message not found",
                    error_code="MESSAGE_NOT_FOUND",
                )

            # Check if user is participant in the message's conversation
            if not ChatAuthorizationService.is_conversation_participant(
                user, message.conversation_id
            ):
                return ServiceResult.failure(
                    "User is not a participant in this conversation",
                    error_code="NOT_PARTICIPANT",
                )

            # Inject message into kwargs
            kwargs["_message"] = message

            return func(*args, **kwargs)

        return wrapper

    return decorator
