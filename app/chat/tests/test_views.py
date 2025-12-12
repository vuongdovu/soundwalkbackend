"""
Comprehensive tests for chat API views.

This module tests all chat view endpoints following TDD principles:
- ConversationViewSet: CRUD, read, leave, transfer-ownership actions
- ParticipantViewSet: Add, remove, update participant operations
- MessageViewSet: Send, list, delete message operations

Test Organization:
    - Each ViewSet has its own test class group
    - Each test validates ONE specific HTTP interaction
    - Tests follow pattern: test_<method>_<scenario>_<expected_outcome>

Testing Philosophy:
    Tests focus on observable HTTP behavior:
    - Response status codes
    - Response body structure
    - Database state changes
    - Authentication/permission enforcement
"""

from rest_framework import status

from authentication.tests.factories import UserFactory
from chat.models import (
    Conversation,
    ConversationType,
    Message,
    MessageType,
    Participant,
    ParticipantRole,
)


# =============================================================================
# URL Constants
# =============================================================================


CONVERSATIONS_URL = "/api/v1/chat/conversations/"


def conversation_detail_url(conversation_id):
    """Generate URL for conversation detail endpoint."""
    return f"{CONVERSATIONS_URL}{conversation_id}/"


def conversation_read_url(conversation_id):
    """Generate URL for mark as read endpoint."""
    return f"{CONVERSATIONS_URL}{conversation_id}/read/"


def conversation_leave_url(conversation_id):
    """Generate URL for leave conversation endpoint."""
    return f"{CONVERSATIONS_URL}{conversation_id}/leave/"


def conversation_transfer_url(conversation_id):
    """Generate URL for transfer ownership endpoint."""
    return f"{CONVERSATIONS_URL}{conversation_id}/transfer-ownership/"


def participants_url(conversation_id):
    """Generate URL for participants list endpoint."""
    return f"{CONVERSATIONS_URL}{conversation_id}/participants/"


def participant_detail_url(conversation_id, participant_id):
    """Generate URL for participant detail endpoint."""
    return f"{CONVERSATIONS_URL}{conversation_id}/participants/{participant_id}/"


def messages_url(conversation_id):
    """Generate URL for messages list endpoint."""
    return f"{CONVERSATIONS_URL}{conversation_id}/messages/"


def message_detail_url(conversation_id, message_id):
    """Generate URL for message detail endpoint."""
    return f"{CONVERSATIONS_URL}{conversation_id}/messages/{message_id}/"


# =============================================================================
# TestConversationViewSetList
# =============================================================================


class TestConversationViewSetList:
    """
    Tests for GET /api/v1/chat/conversations/.

    Verifies:
    - Returns user's conversations only
    - Excludes deleted conversations
    - Pagination support
    - Authentication requirement
    """

    def test_returns_users_conversations(
        self, db, group_conversation_with_members, owner_client, owner_user
    ):
        """
        Returns conversations where user is an active participant.

        Why it matters: Users should only see their own conversations.
        """
        response = owner_client.get(CONVERSATIONS_URL)

        assert response.status_code == status.HTTP_200_OK
        # Check results exist (cursor pagination wraps in results)
        assert "results" in response.data
        conv_ids = [c["id"] for c in response.data["results"]]
        assert group_conversation_with_members.id in conv_ids

    def test_excludes_conversations_user_is_not_in(
        self, db, group_conversation, non_participant_client
    ):
        """
        Non-participants don't see conversations they're not in.

        Why it matters: Privacy - users can't see others' conversations.
        """
        response = non_participant_client.get(CONVERSATIONS_URL)

        assert response.status_code == status.HTTP_200_OK
        conv_ids = [c["id"] for c in response.data["results"]]
        assert group_conversation.id not in conv_ids

    def test_excludes_deleted_conversations(
        self, db, deleted_conversation, owner_client
    ):
        """
        Soft-deleted conversations are excluded from list.

        Why it matters: Deleted conversations should be hidden.
        """
        response = owner_client.get(CONVERSATIONS_URL)

        assert response.status_code == status.HTTP_200_OK
        conv_ids = [c["id"] for c in response.data["results"]]
        assert deleted_conversation.id not in conv_ids

    def test_excludes_left_conversations(
        self, db, group_conversation, owner_client, owner_user
    ):
        """
        Conversations user has left are excluded.

        Why it matters: Left conversations shouldn't appear in list.
        """
        from django.utils import timezone

        # Leave the conversation (update participant directly)
        participant = Participant.objects.get(
            conversation=group_conversation,
            user=owner_user,
        )
        participant.left_at = timezone.now()
        participant.left_voluntarily = True
        participant.save()

        response = owner_client.get(CONVERSATIONS_URL)

        conv_ids = [c["id"] for c in response.data["results"]]
        assert group_conversation.id not in conv_ids

    def test_requires_authentication(self, db, api_client):
        """
        Unauthenticated requests are rejected.

        Why it matters: Conversation list is private data.
        """
        response = api_client.get(CONVERSATIONS_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_includes_pagination(self, db, owner_client):
        """
        Response includes pagination links.

        Why it matters: Cursor pagination for efficient loading.
        """
        response = owner_client.get(CONVERSATIONS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert "next" in response.data
        assert "previous" in response.data


# =============================================================================
# TestConversationViewSetCreate
# =============================================================================


class TestConversationViewSetCreate:
    """
    Tests for POST /api/v1/chat/conversations/.

    Verifies:
    - Direct conversation creation
    - Group conversation creation
    - Returns existing direct conversation
    - Validation errors
    """

    def test_creates_direct_conversation(
        self, db, owner_client, owner_user, other_user
    ):
        """
        Successfully creates direct conversation with another user.

        Why it matters: Primary happy path for starting DMs.
        """
        data = {
            "conversation_type": "direct",
            "participant_ids": [other_user.id],
        }

        response = owner_client.post(CONVERSATIONS_URL, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["conversation_type"] == "direct"
        assert response.data["participant_count"] == 2

    def test_creates_group_conversation(self, db, owner_client, other_user):
        """
        Successfully creates group conversation with title and members.

        Why it matters: Primary happy path for creating groups.
        """
        data = {
            "conversation_type": "group",
            "title": "New Test Group",
            "participant_ids": [other_user.id],
        }

        response = owner_client.post(CONVERSATIONS_URL, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["conversation_type"] == "group"
        assert response.data["title"] == "New Test Group"

    def test_returns_existing_direct_conversation(
        self, db, owner_client, direct_conversation, owner_user, other_user
    ):
        """
        Creating direct conversation with same users returns existing.

        Why it matters: Direct conversations should be unique per user pair.
        """
        data = {
            "conversation_type": "direct",
            "participant_ids": [other_user.id],
        }

        response = owner_client.post(CONVERSATIONS_URL, data, format="json")

        # Should return OK (or 200) with existing conversation
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]
        assert response.data["id"] == direct_conversation.id

    def test_fails_without_participants(self, db, owner_client):
        """
        Direct conversation requires participant_ids.

        Why it matters: Validation - can't create conversation alone.
        """
        data = {
            "conversation_type": "direct",
            "participant_ids": [],
        }

        response = owner_client.post(CONVERSATIONS_URL, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_fails_for_group_without_title(self, db, owner_client, other_user):
        """
        Group conversation requires title.

        Why it matters: Groups need titles for identification.
        """
        data = {
            "conversation_type": "group",
            "participant_ids": [other_user.id],
        }

        response = owner_client.post(CONVERSATIONS_URL, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "title" in response.data

    def test_requires_authentication(self, db, api_client):
        """
        Unauthenticated requests are rejected.
        """
        response = api_client.post(CONVERSATIONS_URL, {})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# =============================================================================
# TestConversationViewSetRetrieve
# =============================================================================


class TestConversationViewSetRetrieve:
    """
    Tests for GET /api/v1/chat/conversations/{id}/.

    Verifies:
    - Returns conversation details for participants
    - Permission denied for non-participants
    - 404 for deleted conversations
    """

    def test_returns_conversation_details(
        self, db, group_conversation_with_members, owner_client
    ):
        """
        Participant receives full conversation details.

        Why it matters: Conversation detail view needs all data.
        """
        url = conversation_detail_url(group_conversation_with_members.id)

        response = owner_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == group_conversation_with_members.id
        assert "participants" in response.data
        assert "current_user_role" in response.data

    def test_includes_current_user_role(
        self, db, group_conversation_with_members, owner_client
    ):
        """
        Response includes current user's role in conversation.

        Why it matters: Frontend needs to know user's permissions.
        """
        url = conversation_detail_url(group_conversation_with_members.id)

        response = owner_client.get(url)

        assert response.data["current_user_role"] == "owner"

    def test_not_found_for_non_participant(
        self, db, group_conversation, non_participant_client
    ):
        """
        Non-participants get 404 (not visible to them).

        Why it matters: Privacy - conversations appear not to exist
        to non-participants rather than showing forbidden.
        """
        url = conversation_detail_url(group_conversation.id)

        response = non_participant_client.get(url)

        # Returns 404 because non-participants can't see the conversation
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_404_for_deleted_conversation(self, db, deleted_conversation, owner_client):
        """
        Deleted conversations return 404.

        Why it matters: Soft-deleted conversations should be hidden.
        """
        url = conversation_detail_url(deleted_conversation.id)

        response = owner_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# TestConversationViewSetPartialUpdate
# =============================================================================


class TestConversationViewSetPartialUpdate:
    """
    Tests for PATCH /api/v1/chat/conversations/{id}/.

    Verifies:
    - Title update by admin/owner
    - Permission enforcement
    - Direct conversation restriction
    """

    def test_owner_can_update_title(self, db, group_conversation, owner_client):
        """
        Owner can update group title.

        Why it matters: Group management capability.
        """
        url = conversation_detail_url(group_conversation.id)

        response = owner_client.patch(url, {"title": "Updated Title"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["title"] == "Updated Title"

    def test_admin_can_update_title(
        self, db, group_conversation_with_members, admin_client
    ):
        """
        Admin can update group title.

        Why it matters: Admins have management permissions.
        """
        url = conversation_detail_url(group_conversation_with_members.id)

        response = admin_client.patch(url, {"title": "Admin Updated"}, format="json")

        assert response.status_code == status.HTTP_200_OK

    def test_member_cannot_update_title(
        self, db, group_conversation_with_members, member_client
    ):
        """
        Members cannot update group title.

        Why it matters: Members have limited permissions.
        """
        url = conversation_detail_url(group_conversation_with_members.id)

        response = member_client.patch(url, {"title": "Member Attempt"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cannot_update_direct_conversation(
        self, db, direct_conversation, owner_client
    ):
        """
        Direct conversations cannot have title updated.

        Why it matters: Direct conversations don't have titles.
        """
        url = conversation_detail_url(direct_conversation.id)

        response = owner_client.patch(url, {"title": "DM Title"}, format="json")

        # Returns 403 from IsGroupConversation permission
        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# TestConversationViewSetDestroy
# =============================================================================


class TestConversationViewSetDestroy:
    """
    Tests for DELETE /api/v1/chat/conversations/{id}/.

    Verifies:
    - Owner can delete group
    - Non-owner cannot delete
    - Cannot delete direct conversation
    """

    def test_owner_can_delete_group(self, db, group_conversation, owner_client):
        """
        Owner can soft delete group conversation.

        Why it matters: Owners have full control.
        """
        url = conversation_detail_url(group_conversation.id)

        response = owner_client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        group_conversation.refresh_from_db()
        assert group_conversation.is_deleted is True

    def test_admin_cannot_delete_group(
        self, db, group_conversation_with_members, admin_client
    ):
        """
        Admin cannot delete group (owner only).

        Why it matters: Delete is a destructive operation.
        """
        url = conversation_detail_url(group_conversation_with_members.id)

        response = admin_client.delete(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cannot_delete_direct_conversation(
        self, db, direct_conversation, owner_client
    ):
        """
        Direct conversations cannot be deleted via this endpoint.

        Why it matters: Use leave action instead.
        """
        url = conversation_detail_url(direct_conversation.id)

        response = owner_client.delete(url)

        # Returns 403 from IsGroupConversation permission
        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# TestConversationViewSetReadAction
# =============================================================================


class TestConversationViewSetReadAction:
    """
    Tests for POST /api/v1/chat/conversations/{id}/read/.

    Verifies:
    - Marks conversation as read
    - Non-participant cannot mark as read
    """

    def test_marks_conversation_as_read(
        self, db, group_conversation, owner_client, owner_user
    ):
        """
        Successfully marks conversation as read.

        Why it matters: Tracks unread state for users.
        """
        url = conversation_read_url(group_conversation.id)

        response = owner_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        participant = Participant.objects.get(
            conversation=group_conversation,
            user=owner_user,
        )
        assert participant.last_read_at is not None

    def test_non_participant_cannot_mark_read(
        self, db, group_conversation, non_participant_client
    ):
        """
        Non-participants cannot mark conversation as read.

        Why it matters: Only participants have read state.
        """
        url = conversation_read_url(group_conversation.id)

        response = non_participant_client.post(url)

        # Returns 404 because conversation not visible to non-participant
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# TestConversationViewSetLeaveAction
# =============================================================================


class TestConversationViewSetLeaveAction:
    """
    Tests for POST /api/v1/chat/conversations/{id}/leave/.

    Verifies:
    - Participant can leave
    - Owner leaving triggers ownership transfer
    - Non-participant cannot leave
    """

    def test_member_can_leave(
        self, db, group_conversation_with_members, member_client, member_user
    ):
        """
        Member can leave the group.

        Why it matters: Basic leave functionality.
        """
        url = conversation_leave_url(group_conversation_with_members.id)

        response = member_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
        )
        assert participant.left_at is not None

    def test_owner_leaving_transfers_ownership(self, db, ownership_transfer_scenario):
        """
        Owner leaving transfers to next eligible participant.

        Why it matters: Groups need continuous ownership.
        """
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken

        scenario = ownership_transfer_scenario
        owner = scenario["owner"]
        admin = scenario["admin"]

        client = APIClient()
        refresh = RefreshToken.for_user(owner)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        url = conversation_leave_url(scenario["conversation"].id)
        response = client.post(url)

        assert response.status_code == status.HTTP_200_OK
        admin_participant = Participant.objects.get(
            conversation=scenario["conversation"],
            user=admin,
            left_at__isnull=True,
        )
        assert admin_participant.role == ParticipantRole.OWNER

    def test_non_participant_cannot_leave(
        self, db, group_conversation, non_participant_client
    ):
        """
        Non-participants get error when trying to leave.

        Why it matters: Can't leave what you're not part of.
        """
        url = conversation_leave_url(group_conversation.id)

        response = non_participant_client.post(url)

        # Returns 404 because conversation not visible to non-participant
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# TestConversationViewSetTransferAction
# =============================================================================


class TestConversationViewSetTransferAction:
    """
    Tests for POST /api/v1/chat/conversations/{id}/transfer-ownership/.

    Verifies:
    - Owner can transfer ownership
    - Non-owner cannot transfer
    - Cannot transfer to non-participant
    """

    def test_owner_can_transfer_to_admin(
        self, db, group_conversation_with_members, owner_client, owner_user, admin_user
    ):
        """
        Owner can transfer ownership to an admin.

        Why it matters: Orderly succession of group ownership.
        """
        url = conversation_transfer_url(group_conversation_with_members.id)

        response = owner_client.post(url, {"user_id": admin_user.id}, format="json")

        assert response.status_code == status.HTTP_200_OK
        admin_participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=admin_user,
            left_at__isnull=True,
        )
        assert admin_participant.role == ParticipantRole.OWNER

    def test_non_owner_cannot_transfer(
        self, db, group_conversation_with_members, admin_client, member_user
    ):
        """
        Non-owner cannot transfer ownership.

        Why it matters: Only current owner can transfer.
        """
        url = conversation_transfer_url(group_conversation_with_members.id)

        response = admin_client.post(url, {"user_id": member_user.id}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cannot_transfer_to_non_participant(
        self, db, group_conversation, owner_client, non_participant_user
    ):
        """
        Cannot transfer to someone not in the group.

        Why it matters: New owner must be an existing participant.
        """
        url = conversation_transfer_url(group_conversation.id)

        response = owner_client.post(
            url, {"user_id": non_participant_user.id}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# TestParticipantViewSetList
# =============================================================================


class TestParticipantViewSetList:
    """
    Tests for GET /api/v1/chat/conversations/{id}/participants/.

    Verifies:
    - Returns active participants
    - Excludes left participants
    - Permission enforcement
    """

    def test_returns_active_participants(
        self, db, group_conversation_with_members, owner_client
    ):
        """
        Returns list of active participants.

        Why it matters: Participant list for conversation display.
        """
        url = participants_url(group_conversation_with_members.id)

        response = owner_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

    def test_excludes_left_participants(
        self, db, group_conversation, owner_client, left_participant
    ):
        """
        Left participants are excluded from list.

        Why it matters: Only active members shown.
        """
        url = participants_url(group_conversation.id)

        response = owner_client.get(url)

        participant_ids = [p["id"] for p in response.data["results"]]
        assert left_participant.id not in participant_ids

    def test_non_participant_can_view_public_participant_list(
        self, db, group_conversation, non_participant_client
    ):
        """
        Non-participants can view participant list.

        Note: This is the current implementation behavior.
        Conversation existence is not hidden, only write operations
        are restricted. Consider if this should be restricted.
        """
        url = participants_url(group_conversation.id)

        response = non_participant_client.get(url)

        # Current implementation allows listing (no object-level permission check)
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# TestParticipantViewSetCreate
# =============================================================================


class TestParticipantViewSetCreate:
    """
    Tests for POST /api/v1/chat/conversations/{id}/participants/.

    Verifies:
    - Admin/owner can add participants
    - Permission hierarchy for role assignment
    - Cannot add to direct conversation
    """

    def test_owner_can_add_member(
        self, db, group_conversation, owner_client, non_participant_user
    ):
        """
        Owner can add new member.

        Why it matters: Basic member addition.
        """
        url = participants_url(group_conversation.id)

        response = owner_client.post(
            url,
            {"user_id": non_participant_user.id, "role": "member"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Participant.objects.filter(
            conversation=group_conversation,
            user=non_participant_user,
            left_at__isnull=True,
        ).exists()

    def test_admin_can_add_member(
        self, db, group_conversation_with_members, admin_client
    ):
        """
        Admin can add new member.

        Why it matters: Admin-level permission.
        """
        new_user = UserFactory(email_verified=True)
        url = participants_url(group_conversation_with_members.id)

        response = admin_client.post(
            url,
            {"user_id": new_user.id, "role": "member"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED

    def test_admin_cannot_add_admin(
        self, db, group_conversation_with_members, admin_client
    ):
        """
        Admin cannot grant admin role.

        Why it matters: Privilege escalation prevention.
        """
        new_user = UserFactory(email_verified=True)
        url = participants_url(group_conversation_with_members.id)

        response = admin_client.post(
            url,
            {"user_id": new_user.id, "role": "admin"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_member_cannot_add_anyone(
        self, db, group_conversation_with_members, member_client
    ):
        """
        Members cannot add participants.

        Why it matters: Members have limited permissions.
        """
        new_user = UserFactory(email_verified=True)
        url = participants_url(group_conversation_with_members.id)

        response = member_client.post(
            url,
            {"user_id": new_user.id},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cannot_add_to_direct(self, db, direct_conversation, owner_client):
        """
        Cannot add participants to direct conversation.

        Why it matters: Direct conversations are always 2 people.
        """
        new_user = UserFactory(email_verified=True)
        url = participants_url(direct_conversation.id)

        response = owner_client.post(
            url,
            {"user_id": new_user.id},
            format="json",
        )

        # Returns 403 from IsGroupConversation permission
        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# TestParticipantViewSetPartialUpdate
# =============================================================================


class TestParticipantViewSetPartialUpdate:
    """
    Tests for PATCH /api/v1/chat/conversations/{id}/participants/{pk}/.

    Verifies:
    - Only owner can change roles
    - Valid role transitions
    """

    def test_owner_can_promote_to_admin(
        self, db, group_conversation_with_members, owner_client, member_user
    ):
        """
        Owner can promote member to admin.

        Why it matters: Role management capability.
        """
        member_participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
        )
        url = participant_detail_url(
            group_conversation_with_members.id, member_participant.id
        )

        response = owner_client.patch(url, {"role": "admin"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        member_participant.refresh_from_db()
        assert member_participant.role == ParticipantRole.ADMIN

    def test_owner_can_demote_admin(
        self, db, group_conversation_with_members, owner_client, admin_user
    ):
        """
        Owner can demote admin to member.

        Why it matters: Owner can revoke admin privileges.
        """
        admin_participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=admin_user,
        )
        url = participant_detail_url(
            group_conversation_with_members.id, admin_participant.id
        )

        response = owner_client.patch(url, {"role": "member"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        admin_participant.refresh_from_db()
        assert admin_participant.role == ParticipantRole.MEMBER

    def test_admin_cannot_change_roles(
        self, db, group_conversation_with_members, admin_client, member_user
    ):
        """
        Admin cannot change participant roles.

        Why it matters: Role management is owner-only.
        """
        member_participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
        )
        url = participant_detail_url(
            group_conversation_with_members.id, member_participant.id
        )

        response = admin_client.patch(url, {"role": "admin"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# TestParticipantViewSetDestroy
# =============================================================================


class TestParticipantViewSetDestroy:
    """
    Tests for DELETE /api/v1/chat/conversations/{id}/participants/{pk}/.

    Verifies:
    - Permission hierarchy for removal
    - Cannot remove owner
    """

    def test_owner_can_remove_member(
        self, db, group_conversation_with_members, owner_client, member_user
    ):
        """
        Owner can remove a member.

        Why it matters: Member management capability.
        """
        member_participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
        )
        url = participant_detail_url(
            group_conversation_with_members.id, member_participant.id
        )

        response = owner_client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        member_participant.refresh_from_db()
        assert member_participant.left_at is not None

    def test_admin_can_remove_member(
        self, db, group_conversation_with_members, admin_client, member_user
    ):
        """
        Admin can remove a member.

        Why it matters: Admin-level member management.
        """
        member_participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
        )
        url = participant_detail_url(
            group_conversation_with_members.id, member_participant.id
        )

        response = admin_client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_admin_cannot_remove_admin(self, db):
        """
        Admin cannot remove another admin.

        Why it matters: Admins are peers.
        """
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken

        owner = UserFactory(email_verified=True)
        admin1 = UserFactory(email_verified=True)
        admin2 = UserFactory(email_verified=True)

        conv = Conversation.objects.create(
            conversation_type=ConversationType.GROUP,
            title="Test",
            created_by=owner,
            participant_count=3,
        )
        Participant.objects.create(
            conversation=conv, user=owner, role=ParticipantRole.OWNER
        )
        Participant.objects.create(
            conversation=conv, user=admin1, role=ParticipantRole.ADMIN
        )
        admin2_part = Participant.objects.create(
            conversation=conv, user=admin2, role=ParticipantRole.ADMIN
        )

        client = APIClient()
        refresh = RefreshToken.for_user(admin1)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        url = participant_detail_url(conv.id, admin2_part.id)
        response = client.delete(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# TestMessageViewSetList
# =============================================================================


class TestMessageViewSetList:
    """
    Tests for GET /api/v1/chat/conversations/{id}/messages/.

    Verifies:
    - Returns conversation messages
    - Cursor pagination
    - Permission enforcement
    """

    def test_returns_conversation_messages(
        self, db, text_message, group_conversation, owner_client
    ):
        """
        Returns messages in the conversation.

        Why it matters: Message list for conversation display.
        """
        url = messages_url(group_conversation.id)

        response = owner_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data
        message_ids = [m["id"] for m in response.data["results"]]
        assert text_message.id in message_ids

    def test_non_participant_cannot_list_messages(
        self, db, group_conversation, non_participant_client
    ):
        """
        Non-participants cannot view messages.

        Why it matters: Conversation content is private.
        """
        url = messages_url(group_conversation.id)

        response = non_participant_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_includes_cursor_pagination(
        self, db, conversation_with_messages, owner_client
    ):
        """
        Response includes cursor pagination.

        Why it matters: Efficient loading for long message lists.
        """
        conv = conversation_with_messages["conversation"]
        url = messages_url(conv.id)

        response = owner_client.get(url)

        assert "next" in response.data
        assert "previous" in response.data


# =============================================================================
# TestMessageViewSetCreate
# =============================================================================


class TestMessageViewSetCreate:
    """
    Tests for POST /api/v1/chat/conversations/{id}/messages/.

    Verifies:
    - Send text message
    - Send reply
    - Permission enforcement
    """

    def test_sends_text_message(self, db, group_conversation, owner_client, owner_user):
        """
        Participant can send a text message.

        Why it matters: Basic messaging functionality.
        """
        url = messages_url(group_conversation.id)

        response = owner_client.post(
            url,
            {"content": "Hello, world!"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["content"] == "Hello, world!"
        assert Message.objects.filter(
            conversation=group_conversation,
            sender=owner_user,
            content="Hello, world!",
        ).exists()

    def test_sends_reply_message(
        self, db, text_message, group_conversation, owner_client
    ):
        """
        Can send reply to existing message.

        Why it matters: Threading support.
        """
        url = messages_url(group_conversation.id)

        response = owner_client.post(
            url,
            {"content": "This is a reply", "parent_id": text_message.id},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["parent_id"] == text_message.id

    def test_reply_to_reply_references_root(
        self, db, threading_scenario, authenticated_client_factory
    ):
        """
        Reply to a reply references the root (single-level threading).

        Why it matters: Prevents deeply nested threads.
        """
        scenario = threading_scenario
        reply = scenario["reply"]
        root = scenario["root"]

        client = authenticated_client_factory(scenario["owner"])
        url = messages_url(scenario["conversation"].id)

        response = client.post(
            url,
            {"content": "Reply to reply", "parent_id": reply.id},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        # Should reference root, not the reply
        assert response.data["parent_id"] == root.id

    def test_non_participant_cannot_send(
        self, db, group_conversation, non_participant_client
    ):
        """
        Non-participants cannot send messages.

        Why it matters: Only members can contribute.
        """
        url = messages_url(group_conversation.id)

        response = non_participant_client.post(
            url,
            {"content": "Unauthorized message"},
            format="json",
        )

        # Returns 400 because service layer rejects non-participant
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "NOT_PARTICIPANT" in response.data.get("error_code", "")

    def test_cannot_send_empty_message(self, db, group_conversation, owner_client):
        """
        Empty messages are rejected.

        Why it matters: Messages should have content.
        """
        url = messages_url(group_conversation.id)

        response = owner_client.post(url, {"content": ""}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# TestMessageViewSetDestroy
# =============================================================================


class TestMessageViewSetDestroy:
    """
    Tests for DELETE /api/v1/chat/conversations/{id}/messages/{pk}/.

    Verifies:
    - Sender can delete own message
    - Owner can delete any message
    - Soft delete preserves content
    """

    def test_sender_can_delete_own_message(
        self, db, text_message, group_conversation, owner_client
    ):
        """
        Message sender can delete their own message.

        Why it matters: Users control their own content.
        """
        url = message_detail_url(group_conversation.id, text_message.id)

        response = owner_client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        text_message.refresh_from_db()
        assert text_message.is_deleted is True

    def test_group_owner_cannot_delete_others_message_via_api(
        self, db, group_conversation_with_members, owner_client, member_user
    ):
        """
        Group owner cannot delete others' messages via API permission check.

        Note: The permission layer restricts deletion to message sender only.
        While the service layer supports owner moderation, the permission
        class CanModifyMessage doesn't allow owners to delete others' messages.

        This test documents the current behavior - consider updating permissions
        if owner moderation via API is desired.
        """
        message = Message.objects.create(
            conversation=group_conversation_with_members,
            sender=member_user,
            message_type=MessageType.TEXT,
            content="Member's message",
        )
        url = message_detail_url(group_conversation_with_members.id, message.id)

        response = owner_client.delete(url)

        # Permission check prevents owner from deleting others' messages
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_member_cannot_delete_others_message(
        self, db, group_conversation_with_members, member_client, owner_user
    ):
        """
        Members cannot delete other users' messages.

        Why it matters: Users can only control their own content.
        """
        message = Message.objects.create(
            conversation=group_conversation_with_members,
            sender=owner_user,
            message_type=MessageType.TEXT,
            content="Owner's message",
        )
        url = message_detail_url(group_conversation_with_members.id, message.id)

        response = member_client.delete(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cannot_delete_system_message(
        self, db, system_message, group_conversation, owner_client
    ):
        """
        System messages cannot be deleted.

        Why it matters: System messages are audit trail.
        """
        url = message_detail_url(group_conversation.id, system_message.id)

        response = owner_client.delete(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_soft_delete_preserves_content(
        self, db, text_message, group_conversation, owner_client
    ):
        """
        Deleted message content is preserved in database.

        Why it matters: Audit trail and potential recovery.
        """
        original_content = text_message.content
        url = message_detail_url(group_conversation.id, text_message.id)

        owner_client.delete(url)

        text_message.refresh_from_db()
        assert text_message.content == original_content
        assert text_message.is_deleted is True
