"""
Test configuration and fixtures for chat tests.

This module provides:
- Reusable fixtures for common chat test scenarios
- User fixtures with different conversation roles
- Conversation fixtures (direct and group)
- Message fixtures (text and system)
- API client helpers for authenticated requests

Usage:
    def test_example(group_conversation, owner_client):
        response = owner_client.get(f'/api/v1/chat/conversations/{group_conversation.id}/')
        assert response.status_code == 200
"""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.tests.factories import UserFactory
from chat.models import (
    Conversation,
    ConversationType,
    DirectConversationPair,
    Message,
    MessageType,
    Participant,
    ParticipantRole,
)


# =============================================================================
# User Fixtures
# =============================================================================


@pytest.fixture
def owner_user(db):
    """Create a user who will be a conversation owner."""
    return UserFactory(email_verified=True)


@pytest.fixture
def admin_user(db):
    """Create a user who will be a conversation admin."""
    return UserFactory(email_verified=True)


@pytest.fixture
def member_user(db):
    """Create a user who will be a conversation member."""
    return UserFactory(email_verified=True)


@pytest.fixture
def other_user(db):
    """Create another user for various tests."""
    return UserFactory(email_verified=True)


@pytest.fixture
def non_participant_user(db):
    """Create a user who is not a participant in any test conversation."""
    return UserFactory(email_verified=True)


# =============================================================================
# Conversation Fixtures
# =============================================================================


@pytest.fixture
def group_conversation(db, owner_user):
    """
    Create a group conversation with an owner.

    Returns a group conversation with the owner_user as the owner.
    """
    conversation = Conversation.objects.create(
        conversation_type=ConversationType.GROUP,
        title="Test Group",
        created_by=owner_user,
        participant_count=1,
    )
    Participant.objects.create(
        conversation=conversation,
        user=owner_user,
        role=ParticipantRole.OWNER,
    )
    return conversation


@pytest.fixture
def group_conversation_with_members(db, owner_user, admin_user, member_user):
    """
    Create a group conversation with owner, admin, and member.

    Provides a full role hierarchy for permission testing.
    """
    conversation = Conversation.objects.create(
        conversation_type=ConversationType.GROUP,
        title="Test Group with Members",
        created_by=owner_user,
        participant_count=3,
    )

    # Create participants with different roles
    Participant.objects.create(
        conversation=conversation,
        user=owner_user,
        role=ParticipantRole.OWNER,
    )
    Participant.objects.create(
        conversation=conversation,
        user=admin_user,
        role=ParticipantRole.ADMIN,
    )
    Participant.objects.create(
        conversation=conversation,
        user=member_user,
        role=ParticipantRole.MEMBER,
    )

    return conversation


@pytest.fixture
def direct_conversation(db, owner_user, other_user):
    """
    Create a direct conversation between two users.

    Uses owner_user and other_user as the participants.
    """
    # Ensure canonical order
    user_lower, user_higher = (
        (owner_user, other_user)
        if owner_user.id < other_user.id
        else (other_user, owner_user)
    )

    conversation = Conversation.objects.create(
        conversation_type=ConversationType.DIRECT,
        title="",
        created_by=None,
        participant_count=2,
    )

    DirectConversationPair.objects.create(
        conversation=conversation,
        user_lower=user_lower,
        user_higher=user_higher,
    )

    Participant.objects.create(
        conversation=conversation,
        user=user_lower,
        role=None,
    )
    Participant.objects.create(
        conversation=conversation,
        user=user_higher,
        role=None,
    )

    return conversation


@pytest.fixture
def deleted_conversation(db, owner_user):
    """Create a soft-deleted group conversation."""
    conversation = Conversation.objects.create(
        conversation_type=ConversationType.GROUP,
        title="Deleted Group",
        created_by=owner_user,
        participant_count=1,
        is_deleted=True,
        deleted_at=timezone.now(),
    )
    Participant.objects.create(
        conversation=conversation,
        user=owner_user,
        role=ParticipantRole.OWNER,
    )
    return conversation


# =============================================================================
# Participant Fixtures
# =============================================================================


@pytest.fixture
def owner_participant(db, group_conversation, owner_user):
    """Get the owner participant of the group conversation."""
    return Participant.objects.get(
        conversation=group_conversation,
        user=owner_user,
        left_at__isnull=True,
    )


@pytest.fixture
def admin_participant(db, group_conversation_with_members, admin_user):
    """Get the admin participant of the group conversation with members."""
    return Participant.objects.get(
        conversation=group_conversation_with_members,
        user=admin_user,
        left_at__isnull=True,
    )


@pytest.fixture
def member_participant(db, group_conversation_with_members, member_user):
    """Get the member participant of the group conversation with members."""
    return Participant.objects.get(
        conversation=group_conversation_with_members,
        user=member_user,
        left_at__isnull=True,
    )


@pytest.fixture
def left_participant(db, group_conversation, owner_user):
    """Create a participant who has left the conversation."""
    user = UserFactory(email_verified=True)
    return Participant.objects.create(
        conversation=group_conversation,
        user=user,
        role=ParticipantRole.MEMBER,
        left_at=timezone.now(),
        left_voluntarily=True,
    )


# =============================================================================
# Message Fixtures
# =============================================================================


@pytest.fixture
def text_message(db, group_conversation, owner_user):
    """Create a text message in the group conversation."""
    return Message.objects.create(
        conversation=group_conversation,
        sender=owner_user,
        message_type=MessageType.TEXT,
        content="Hello, this is a test message.",
    )


@pytest.fixture
def deleted_message(db, group_conversation, owner_user):
    """Create a soft-deleted message."""
    return Message.objects.create(
        conversation=group_conversation,
        sender=owner_user,
        message_type=MessageType.TEXT,
        content="This message was deleted.",
        is_deleted=True,
        deleted_at=timezone.now(),
    )


@pytest.fixture
def system_message(db, group_conversation):
    """Create a system message in the group conversation."""
    import json

    return Message.objects.create(
        conversation=group_conversation,
        sender=None,
        message_type=MessageType.SYSTEM,
        content=json.dumps(
            {"event": "group_created", "data": {"title": group_conversation.title}}
        ),
    )


@pytest.fixture
def message_with_reply(db, group_conversation, owner_user, member_user):
    """Create a message with a reply (for threading tests)."""
    # Create root message
    root = Message.objects.create(
        conversation=group_conversation,
        sender=owner_user,
        message_type=MessageType.TEXT,
        content="This is the root message.",
        reply_count=1,
    )

    # Create reply
    reply = Message.objects.create(
        conversation=group_conversation,
        sender=member_user,
        message_type=MessageType.TEXT,
        content="This is a reply.",
        parent_message=root,
    )

    return {"root": root, "reply": reply}


@pytest.fixture
def conversation_with_messages(
    db, group_conversation_with_members, owner_user, admin_user, member_user
):
    """Create a conversation with multiple messages for pagination testing."""
    messages = []
    users = [owner_user, admin_user, member_user]

    for i in range(25):
        msg = Message.objects.create(
            conversation=group_conversation_with_members,
            sender=users[i % 3],
            message_type=MessageType.TEXT,
            content=f"Message number {i + 1}",
        )
        messages.append(msg)

    # Update conversation last_message_at
    group_conversation_with_members.last_message_at = messages[-1].created_at
    group_conversation_with_members.save(update_fields=["last_message_at"])

    return {
        "conversation": group_conversation_with_members,
        "messages": messages,
    }


# =============================================================================
# API Client Fixtures
# =============================================================================


@pytest.fixture
def api_client():
    """Unauthenticated API client for public endpoints."""
    return APIClient()


@pytest.fixture
def authenticated_client_factory(db):
    """
    Factory to create authenticated clients for any user.

    Usage:
        def test_example(authenticated_client_factory, some_user):
            client = authenticated_client_factory(some_user)
            response = client.get('/api/v1/chat/conversations/')
    """

    def _make_client(user):
        client = APIClient()
        refresh = RefreshToken.for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client

    return _make_client


@pytest.fixture
def owner_client(authenticated_client_factory, owner_user):
    """API client authenticated as the owner user."""
    return authenticated_client_factory(owner_user)


@pytest.fixture
def admin_client(authenticated_client_factory, admin_user):
    """API client authenticated as the admin user."""
    return authenticated_client_factory(admin_user)


@pytest.fixture
def member_client(authenticated_client_factory, member_user):
    """API client authenticated as the member user."""
    return authenticated_client_factory(member_user)


@pytest.fixture
def other_client(authenticated_client_factory, other_user):
    """API client authenticated as the other user."""
    return authenticated_client_factory(other_user)


@pytest.fixture
def non_participant_client(authenticated_client_factory, non_participant_user):
    """API client authenticated as a non-participant user."""
    return authenticated_client_factory(non_participant_user)


# =============================================================================
# Test Data Fixtures
# =============================================================================


@pytest.fixture
def valid_group_create_data(other_user):
    """Valid data for creating a group conversation."""
    return {
        "conversation_type": "group",
        "title": "New Test Group",
        "participant_ids": [other_user.id],
    }


@pytest.fixture
def valid_direct_create_data(other_user):
    """Valid data for creating a direct conversation."""
    return {
        "conversation_type": "direct",
        "participant_ids": [other_user.id],
    }


@pytest.fixture
def valid_message_data():
    """Valid data for sending a message."""
    return {
        "content": "This is a test message.",
    }


@pytest.fixture
def valid_reply_data(text_message):
    """Valid data for sending a reply message."""
    return {
        "content": "This is a reply.",
        "parent_id": text_message.id,
    }


@pytest.fixture
def valid_participant_data(non_participant_user):
    """Valid data for adding a participant."""
    return {
        "user_id": non_participant_user.id,
        "role": "member",
    }


# =============================================================================
# Scenario Fixtures
# =============================================================================


@pytest.fixture
def ownership_transfer_scenario(db):
    """
    Create a scenario for testing ownership transfer.

    Returns a dict with:
    - conversation: Group conversation
    - owner: Current owner user
    - admin: Admin who could become owner
    - member: Member who could become owner
    """
    owner = UserFactory(email_verified=True)
    admin = UserFactory(email_verified=True)
    member = UserFactory(email_verified=True)

    conversation = Conversation.objects.create(
        conversation_type=ConversationType.GROUP,
        title="Ownership Transfer Test",
        created_by=owner,
        participant_count=3,
    )

    Participant.objects.create(
        conversation=conversation,
        user=owner,
        role=ParticipantRole.OWNER,
    )
    Participant.objects.create(
        conversation=conversation,
        user=admin,
        role=ParticipantRole.ADMIN,
    )
    Participant.objects.create(
        conversation=conversation,
        user=member,
        role=ParticipantRole.MEMBER,
    )

    return {
        "conversation": conversation,
        "owner": owner,
        "admin": admin,
        "member": member,
    }


@pytest.fixture
def threading_scenario(db, owner_user, member_user):
    """
    Create a scenario for testing message threading.

    Returns a dict with:
    - conversation: Group conversation
    - root: Root message
    - reply: Reply to root
    - owner: Owner user
    - member: Member user
    """
    conversation = Conversation.objects.create(
        conversation_type=ConversationType.GROUP,
        title="Threading Test",
        created_by=owner_user,
        participant_count=2,
    )

    Participant.objects.create(
        conversation=conversation,
        user=owner_user,
        role=ParticipantRole.OWNER,
    )
    Participant.objects.create(
        conversation=conversation,
        user=member_user,
        role=ParticipantRole.MEMBER,
    )

    root = Message.objects.create(
        conversation=conversation,
        sender=owner_user,
        message_type=MessageType.TEXT,
        content="Root message",
        reply_count=1,
    )

    reply = Message.objects.create(
        conversation=conversation,
        sender=member_user,
        message_type=MessageType.TEXT,
        content="Reply to root",
        parent_message=root,
    )

    return {
        "conversation": conversation,
        "root": root,
        "reply": reply,
        "owner": owner_user,
        "member": member_user,
    }
