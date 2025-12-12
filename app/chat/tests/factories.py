"""
Factory Boy factories for chat models.

Provides realistic test data generation for:
- Conversation: Direct and group conversations
- DirectConversationPair: Helper for direct conversation uniqueness
- Participant: User participation in conversations
- Message: Text and system messages

Usage:
    from chat.tests.factories import (
        ConversationFactory,
        DirectConversationFactory,
        GroupConversationFactory,
        ParticipantFactory,
        MessageFactory,
    )

    # Create a group conversation with owner
    conversation = GroupConversationFactory()

    # Create a direct conversation between two users
    conversation = DirectConversationFactory()

    # Create a message in a conversation
    message = MessageFactory(conversation=conversation, sender=user)
"""

import json

import factory

from authentication.tests.factories import UserFactory
from chat.models import (
    Conversation,
    ConversationType,
    DirectConversationPair,
    Message,
    MessageType,
    Participant,
    ParticipantRole,
    SystemMessageEvent,
)


class ConversationFactory(factory.django.DjangoModelFactory):
    """
    Base factory for Conversation model.

    Creates a basic group conversation by default.
    Use DirectConversationFactory or GroupConversationFactory for specific types.

    Examples:
        # Basic group conversation
        conversation = ConversationFactory()

        # Group with specific title
        conversation = ConversationFactory(title="Project Team")

        # Deleted conversation
        conversation = ConversationFactory(is_deleted=True)
    """

    class Meta:
        model = Conversation

    conversation_type = ConversationType.GROUP
    title = factory.Sequence(lambda n: f"Group Chat {n}")
    created_by = factory.SubFactory(UserFactory)
    participant_count = 0
    last_message_at = None
    is_deleted = False
    deleted_at = None


class GroupConversationFactory(ConversationFactory):
    """
    Factory for group conversations with owner participant.

    Creates a group conversation and automatically adds the creator
    as the owner participant.

    Examples:
        # Group with owner
        conversation = GroupConversationFactory()
        owner = conversation.participants.get(role=ParticipantRole.OWNER)

        # Group with specific creator
        conversation = GroupConversationFactory(created_by=specific_user)
    """

    @factory.post_generation
    def add_owner(self, create, extracted, **kwargs):
        """Add the creator as owner after conversation is created."""
        if not create:
            return

        # Create owner participant
        ParticipantFactory(
            conversation=self,
            user=self.created_by,
            role=ParticipantRole.OWNER,
        )
        # Update participant count
        self.participant_count = 1
        self.save(update_fields=["participant_count"])


class DirectConversationFactory(factory.django.DjangoModelFactory):
    """
    Factory for direct (1:1) conversations.

    Creates a direct conversation between two users with the
    DirectConversationPair for uniqueness enforcement.

    Examples:
        # Direct conversation between two random users
        conversation = DirectConversationFactory()

        # Direct conversation between specific users
        conversation = DirectConversationFactory(user1=alice, user2=bob)
    """

    class Meta:
        model = Conversation

    conversation_type = ConversationType.DIRECT
    title = ""
    created_by = None
    participant_count = 2
    last_message_at = None
    is_deleted = False
    deleted_at = None

    class Params:
        # Users for the direct conversation
        user1 = factory.SubFactory(UserFactory)
        user2 = factory.SubFactory(UserFactory)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Create direct conversation with participants and pair."""
        user1 = kwargs.pop("user1", None) or UserFactory()
        user2 = kwargs.pop("user2", None) or UserFactory()

        # Ensure canonical order (lower ID first)
        user_lower, user_higher = (
            (user1, user2) if user1.id < user2.id else (user2, user1)
        )

        # Create conversation
        conversation = super()._create(model_class, *args, **kwargs)

        # Create DirectConversationPair
        DirectConversationPair.objects.create(
            conversation=conversation,
            user_lower=user_lower,
            user_higher=user_higher,
        )

        # Create participants (no roles for direct)
        ParticipantFactory(
            conversation=conversation,
            user=user_lower,
            role=None,
        )
        ParticipantFactory(
            conversation=conversation,
            user=user_higher,
            role=None,
        )

        return conversation


class ParticipantFactory(factory.django.DjangoModelFactory):
    """
    Factory for Participant model.

    Creates participant records for conversation membership.

    Examples:
        # Active member
        participant = ParticipantFactory(
            conversation=conversation,
            user=user,
            role=ParticipantRole.MEMBER
        )

        # Admin participant
        participant = ParticipantFactory(role=ParticipantRole.ADMIN)

        # Left participant
        participant = ParticipantFactory(
            left_at=timezone.now(),
            left_voluntarily=True
        )
    """

    class Meta:
        model = Participant

    conversation = factory.SubFactory(ConversationFactory)
    user = factory.SubFactory(UserFactory)
    role = ParticipantRole.MEMBER
    left_at = None
    left_voluntarily = None
    removed_by = None
    last_read_at = None


class MessageFactory(factory.django.DjangoModelFactory):
    """
    Factory for Message model.

    Creates text messages by default.
    Use SystemMessageFactory for system event messages.

    Examples:
        # Text message
        message = MessageFactory(conversation=conv, sender=user)

        # Reply to another message
        message = MessageFactory(parent_message=root_message)

        # Deleted message
        message = MessageFactory(is_deleted=True)
    """

    class Meta:
        model = Message

    conversation = factory.SubFactory(ConversationFactory)
    sender = factory.SubFactory(UserFactory)
    message_type = MessageType.TEXT
    content = factory.Faker("paragraph", nb_sentences=2)
    parent_message = None
    reply_count = 0
    is_deleted = False
    deleted_at = None


class SystemMessageFactory(MessageFactory):
    """
    Factory for system event messages.

    Creates system messages with JSON content containing event data.

    Examples:
        # Group created message
        message = SystemMessageFactory.create_group_created(
            conversation=conv,
            title="New Group"
        )

        # Participant added message
        message = SystemMessageFactory.create_participant_added(
            conversation=conv,
            user_id=user.id,
            added_by_id=admin.id
        )
    """

    sender = None
    message_type = MessageType.SYSTEM

    @classmethod
    def create_group_created(cls, conversation, title):
        """Create a GROUP_CREATED system message."""
        content = json.dumps(
            {"event": SystemMessageEvent.GROUP_CREATED, "data": {"title": title}}
        )
        return cls(conversation=conversation, content=content)

    @classmethod
    def create_participant_added(cls, conversation, user_id, added_by_id):
        """Create a PARTICIPANT_ADDED system message."""
        content = json.dumps(
            {
                "event": SystemMessageEvent.PARTICIPANT_ADDED,
                "data": {"user_id": user_id, "added_by_id": added_by_id},
            }
        )
        return cls(conversation=conversation, content=content)

    @classmethod
    def create_participant_removed(cls, conversation, user_id, removed_by_id, reason):
        """Create a PARTICIPANT_REMOVED system message."""
        content = json.dumps(
            {
                "event": SystemMessageEvent.PARTICIPANT_REMOVED,
                "data": {
                    "user_id": user_id,
                    "removed_by_id": removed_by_id,
                    "reason": reason,
                },
            }
        )
        return cls(conversation=conversation, content=content)

    @classmethod
    def create_role_changed(
        cls, conversation, user_id, old_role, new_role, changed_by_id
    ):
        """Create a ROLE_CHANGED system message."""
        content = json.dumps(
            {
                "event": SystemMessageEvent.ROLE_CHANGED,
                "data": {
                    "user_id": user_id,
                    "old_role": old_role,
                    "new_role": new_role,
                    "changed_by_id": changed_by_id,
                },
            }
        )
        return cls(conversation=conversation, content=content)

    @classmethod
    def create_ownership_transferred(
        cls, conversation, from_user_id, to_user_id, reason
    ):
        """Create an OWNERSHIP_TRANSFERRED system message."""
        content = json.dumps(
            {
                "event": SystemMessageEvent.OWNERSHIP_TRANSFERRED,
                "data": {
                    "from_user_id": from_user_id,
                    "to_user_id": to_user_id,
                    "reason": reason,
                },
            }
        )
        return cls(conversation=conversation, content=content)

    @classmethod
    def create_title_changed(cls, conversation, old_title, new_title, changed_by_id):
        """Create a TITLE_CHANGED system message."""
        content = json.dumps(
            {
                "event": SystemMessageEvent.TITLE_CHANGED,
                "data": {
                    "old_title": old_title,
                    "new_title": new_title,
                    "changed_by_id": changed_by_id,
                },
            }
        )
        return cls(conversation=conversation, content=content)
