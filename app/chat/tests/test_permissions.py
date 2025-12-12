"""
Comprehensive tests for chat permission classes.

This module tests the permission matrix following TDD principles:
- IsConversationParticipant: Active participant check
- IsConversationOwner: Owner role check
- IsConversationAdminOrOwner: Admin/owner role check
- CanManageParticipants: Hierarchical participant management
- CanModifyMessage: Message modification permissions
- IsGroupConversation: Group-only operations

Test Organization:
    - Each permission class has its own test class
    - Tests cover the full permission hierarchy: OWNER > ADMIN > MEMBER
    - Tests verify both has_permission and has_object_permission

Permission Hierarchy Reminder:
    OWNER can:
        - All ADMIN permissions
        - Delete conversation
        - Change participant roles
        - Transfer ownership
        - Add/remove admins

    ADMIN can:
        - All MEMBER permissions
        - Update group title
        - Add members
        - Remove members

    MEMBER can:
        - View conversation
        - Send messages
        - Delete own messages
        - Leave conversation
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIRequestFactory
from rest_framework.request import Request
from rest_framework.views import APIView

from authentication.tests.factories import UserFactory
from chat.models import (
    Conversation,
    ConversationType,
    Participant,
    ParticipantRole,
)
from chat.permissions import (
    CanManageParticipants,
    CanModifyMessage,
    IsConversationAdminOrOwner,
    IsConversationOwner,
    IsConversationParticipant,
    IsGroupConversation,
)


# =============================================================================
# Helper Functions
# =============================================================================


def make_request(user=None, method="GET", data=None):
    """Create a DRF Request object with optional user and method."""
    factory = APIRequestFactory()
    method_func = getattr(factory, method.lower())

    if method.upper() in ["POST", "PUT", "PATCH"]:
        request = method_func("/", data=data or {}, format="json")
    else:
        request = method_func("/")

    drf_request = Request(request)

    if user:
        # Force authentication for the request
        drf_request._user = user
        drf_request._request.user = user
    else:
        drf_request._user = AnonymousUser()
        drf_request._request.user = AnonymousUser()

    return drf_request


class MockView(APIView):
    """Mock view for permission testing."""

    pass


# =============================================================================
# TestIsConversationParticipant
# =============================================================================


class TestIsConversationParticipant:
    """
    Tests for IsConversationParticipant permission.

    This is the base permission requiring active conversation membership.
    """

    def test_allows_active_owner(self, db, group_conversation, owner_user):
        """
        Owner (active participant) is allowed.

        Why it matters: Owners are participants.
        """
        permission = IsConversationParticipant()
        request = make_request(owner_user)
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is True

    def test_allows_active_admin(self, db, group_conversation_with_members, admin_user):
        """
        Admin (active participant) is allowed.

        Why it matters: Admins are participants.
        """
        permission = IsConversationParticipant()
        request = make_request(admin_user)
        view = MockView()

        result = permission.has_object_permission(
            request, view, group_conversation_with_members
        )

        assert result is True

    def test_allows_active_member(
        self, db, group_conversation_with_members, member_user
    ):
        """
        Member (active participant) is allowed.

        Why it matters: Members are participants.
        """
        permission = IsConversationParticipant()
        request = make_request(member_user)
        view = MockView()

        result = permission.has_object_permission(
            request, view, group_conversation_with_members
        )

        assert result is True

    def test_denies_left_participant(self, db, group_conversation, left_participant):
        """
        User who left is denied.

        Why it matters: Left users are no longer active participants.
        """
        permission = IsConversationParticipant()
        request = make_request(left_participant.user)
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is False

    def test_denies_non_participant(self, db, group_conversation, non_participant_user):
        """
        User who was never a participant is denied.

        Why it matters: Non-members shouldn't access conversations.
        """
        permission = IsConversationParticipant()
        request = make_request(non_participant_user)
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is False

    def test_denies_unauthenticated(self, db, group_conversation):
        """
        Unauthenticated request is denied.

        Why it matters: Must be logged in.
        """
        permission = IsConversationParticipant()
        request = make_request(user=None)
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is False

    def test_handles_participant_object(self, db, owner_participant):
        """
        Permission works when passed Participant object instead of Conversation.

        Why it matters: Some views use Participant as the object.
        """
        permission = IsConversationParticipant()
        request = make_request(owner_participant.user)
        view = MockView()

        result = permission.has_object_permission(request, view, owner_participant)

        assert result is True


# =============================================================================
# TestIsConversationOwner
# =============================================================================


class TestIsConversationOwner:
    """
    Tests for IsConversationOwner permission.

    Only allows users with OWNER role.
    """

    def test_allows_owner(self, db, group_conversation, owner_user):
        """
        Owner is allowed.

        Why it matters: Owner check for destructive operations.
        """
        permission = IsConversationOwner()
        request = make_request(owner_user)
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is True

    def test_denies_admin(self, db, group_conversation_with_members, admin_user):
        """
        Admin is denied (not owner).

        Why it matters: Admin != Owner for destructive operations.
        """
        permission = IsConversationOwner()
        request = make_request(admin_user)
        view = MockView()

        result = permission.has_object_permission(
            request, view, group_conversation_with_members
        )

        assert result is False

    def test_denies_member(self, db, group_conversation_with_members, member_user):
        """
        Member is denied.

        Why it matters: Members have limited permissions.
        """
        permission = IsConversationOwner()
        request = make_request(member_user)
        view = MockView()

        result = permission.has_object_permission(
            request, view, group_conversation_with_members
        )

        assert result is False

    def test_denies_non_participant(self, db, group_conversation, non_participant_user):
        """
        Non-participant is denied.

        Why it matters: Must be in conversation to have any role.
        """
        permission = IsConversationOwner()
        request = make_request(non_participant_user)
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is False


# =============================================================================
# TestIsConversationAdminOrOwner
# =============================================================================


class TestIsConversationAdminOrOwner:
    """
    Tests for IsConversationAdminOrOwner permission.

    Allows users with OWNER or ADMIN role.
    """

    def test_allows_owner(self, db, group_conversation, owner_user):
        """
        Owner is allowed.

        Why it matters: Owner has all admin permissions.
        """
        permission = IsConversationAdminOrOwner()
        request = make_request(owner_user)
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is True

    def test_allows_admin(self, db, group_conversation_with_members, admin_user):
        """
        Admin is allowed.

        Why it matters: Admin has management permissions.
        """
        permission = IsConversationAdminOrOwner()
        request = make_request(admin_user)
        view = MockView()

        result = permission.has_object_permission(
            request, view, group_conversation_with_members
        )

        assert result is True

    def test_denies_member(self, db, group_conversation_with_members, member_user):
        """
        Member is denied.

        Why it matters: Members cannot perform admin operations.
        """
        permission = IsConversationAdminOrOwner()
        request = make_request(member_user)
        view = MockView()

        result = permission.has_object_permission(
            request, view, group_conversation_with_members
        )

        assert result is False

    def test_denies_non_participant(self, db, group_conversation, non_participant_user):
        """
        Non-participant is denied.

        Why it matters: Must be in conversation.
        """
        permission = IsConversationAdminOrOwner()
        request = make_request(non_participant_user)
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is False


# =============================================================================
# TestCanManageParticipants
# =============================================================================


class TestCanManageParticipants:
    """
    Tests for CanManageParticipants permission.

    Handles hierarchical permissions for adding/removing participants:
    - OWNER can add anyone (except OWNER), remove anyone
    - ADMIN can add members, remove members
    - MEMBER cannot manage participants
    """

    # -------------------------------------------------------------------------
    # Adding Participants (POST)
    # -------------------------------------------------------------------------

    def test_owner_can_add_admin(self, db, group_conversation, owner_user):
        """
        Owner can add participant with admin role.

        Why it matters: Only owner can grant admin privileges.
        """
        permission = CanManageParticipants()
        request = make_request(
            owner_user, method="POST", data={"role": ParticipantRole.ADMIN}
        )
        # Manually set data since RequestFactory doesn't parse JSON body for POST
        request._full_data = {"role": ParticipantRole.ADMIN}
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is True

    def test_owner_can_add_member(self, db, group_conversation, owner_user):
        """
        Owner can add participant with member role.

        Why it matters: Basic member addition.
        """
        permission = CanManageParticipants()
        request = make_request(
            owner_user, method="POST", data={"role": ParticipantRole.MEMBER}
        )
        request._full_data = {"role": ParticipantRole.MEMBER}
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is True

    def test_owner_cannot_add_owner(self, db, group_conversation, owner_user):
        """
        Cannot add participant with owner role.

        Why it matters: Ownership transfer is a separate operation.
        """
        permission = CanManageParticipants()
        request = make_request(
            owner_user, method="POST", data={"role": ParticipantRole.OWNER}
        )
        request._full_data = {"role": ParticipantRole.OWNER}
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is False

    def test_admin_can_add_member(
        self, db, group_conversation_with_members, admin_user
    ):
        """
        Admin can add participant with member role.

        Why it matters: Admin-level member management.
        """
        permission = CanManageParticipants()
        request = make_request(
            admin_user, method="POST", data={"role": ParticipantRole.MEMBER}
        )
        request._full_data = {"role": ParticipantRole.MEMBER}
        view = MockView()

        result = permission.has_object_permission(
            request, view, group_conversation_with_members
        )

        assert result is True

    def test_admin_cannot_add_admin(
        self, db, group_conversation_with_members, admin_user
    ):
        """
        Admin cannot grant admin role.

        Why it matters: Privilege escalation prevention.
        """
        permission = CanManageParticipants()
        request = make_request(
            admin_user, method="POST", data={"role": ParticipantRole.ADMIN}
        )
        request._full_data = {"role": ParticipantRole.ADMIN}
        view = MockView()

        result = permission.has_object_permission(
            request, view, group_conversation_with_members
        )

        assert result is False

    def test_member_cannot_add_anyone(
        self, db, group_conversation_with_members, member_user
    ):
        """
        Member cannot add participants.

        Why it matters: Members have no management permissions.
        """
        permission = CanManageParticipants()
        request = make_request(
            member_user, method="POST", data={"role": ParticipantRole.MEMBER}
        )
        request._full_data = {"role": ParticipantRole.MEMBER}
        view = MockView()

        result = permission.has_object_permission(
            request, view, group_conversation_with_members
        )

        assert result is False

    # -------------------------------------------------------------------------
    # Removing Participants (DELETE)
    # -------------------------------------------------------------------------

    def test_owner_can_remove_admin(
        self, db, group_conversation_with_members, owner_user, admin_participant
    ):
        """
        Owner can remove an admin.

        Why it matters: Owner has authority over all roles.
        """
        permission = CanManageParticipants()
        request = make_request(owner_user, method="DELETE")
        view = MockView()

        result = permission.has_object_permission(request, view, admin_participant)

        assert result is True

    def test_owner_can_remove_member(
        self, db, group_conversation_with_members, owner_user, member_participant
    ):
        """
        Owner can remove a member.

        Why it matters: Basic member management.
        """
        permission = CanManageParticipants()
        request = make_request(owner_user, method="DELETE")
        view = MockView()

        result = permission.has_object_permission(request, view, member_participant)

        assert result is True

    def test_admin_can_remove_member(
        self, db, group_conversation_with_members, admin_user, member_participant
    ):
        """
        Admin can remove a member.

        Why it matters: Admin-level member management.
        """
        permission = CanManageParticipants()
        request = make_request(admin_user, method="DELETE")
        view = MockView()

        result = permission.has_object_permission(request, view, member_participant)

        assert result is True

    def test_admin_cannot_remove_admin(self, db):
        """
        Admin cannot remove another admin.

        Why it matters: Admins are peers - only owner can remove them.
        """
        owner = UserFactory(email_verified=True)
        admin1 = UserFactory(email_verified=True)
        admin2 = UserFactory(email_verified=True)

        conversation = Conversation.objects.create(
            conversation_type=ConversationType.GROUP,
            title="Test",
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
            user=admin1,
            role=ParticipantRole.ADMIN,
        )
        admin2_participant = Participant.objects.create(
            conversation=conversation,
            user=admin2,
            role=ParticipantRole.ADMIN,
        )

        permission = CanManageParticipants()
        request = make_request(admin1, method="DELETE")
        view = MockView()

        result = permission.has_object_permission(request, view, admin2_participant)

        assert result is False

    def test_cannot_remove_self_via_delete(
        self, db, group_conversation, owner_user, owner_participant
    ):
        """
        Cannot remove yourself via DELETE (use leave endpoint).

        Why it matters: Self-removal has different semantics.
        """
        permission = CanManageParticipants()
        request = make_request(owner_user, method="DELETE")
        view = MockView()

        result = permission.has_object_permission(request, view, owner_participant)

        assert result is False

    # -------------------------------------------------------------------------
    # Updating Roles (PATCH)
    # -------------------------------------------------------------------------

    def test_owner_can_update_role(
        self, db, group_conversation_with_members, owner_user, member_participant
    ):
        """
        Owner can update participant roles.

        Why it matters: Role management is owner-only.
        """
        permission = CanManageParticipants()
        request = make_request(owner_user, method="PATCH")
        view = MockView()

        result = permission.has_object_permission(request, view, member_participant)

        assert result is True

    def test_admin_cannot_update_role(
        self, db, group_conversation_with_members, admin_user, member_participant
    ):
        """
        Admin cannot update participant roles.

        Why it matters: Role management is owner-only.
        """
        permission = CanManageParticipants()
        request = make_request(admin_user, method="PATCH")
        view = MockView()

        result = permission.has_object_permission(request, view, member_participant)

        assert result is False


# =============================================================================
# TestCanModifyMessage
# =============================================================================


class TestCanModifyMessage:
    """
    Tests for CanModifyMessage permission.

    Users can only delete their own messages.
    System messages cannot be deleted.
    """

    def test_sender_can_delete_own_message(self, db, text_message, owner_user):
        """
        Message sender can delete their own message.

        Why it matters: Users control their own content.
        """
        permission = CanModifyMessage()
        request = make_request(owner_user, method="DELETE")
        view = MockView()

        result = permission.has_object_permission(request, view, text_message)

        assert result is True

    def test_other_user_cannot_delete_message(self, db, text_message, member_user):
        """
        Non-sender cannot delete the message.

        Why it matters: Privacy of user content.
        """
        permission = CanModifyMessage()
        request = make_request(member_user, method="DELETE")
        view = MockView()

        result = permission.has_object_permission(request, view, text_message)

        assert result is False

    def test_cannot_delete_system_message(self, db, system_message, owner_user):
        """
        System messages cannot be deleted by anyone.

        Why it matters: System messages are audit trail.
        """
        permission = CanModifyMessage()
        request = make_request(owner_user, method="DELETE")
        view = MockView()

        result = permission.has_object_permission(request, view, system_message)

        assert result is False

    def test_denies_unauthenticated(self, db, text_message):
        """
        Unauthenticated users cannot delete messages.

        Why it matters: Must be logged in.
        """
        permission = CanModifyMessage()
        request = make_request(user=None, method="DELETE")
        view = MockView()

        result = permission.has_object_permission(request, view, text_message)

        assert result is False


# =============================================================================
# TestIsGroupConversation
# =============================================================================


class TestIsGroupConversation:
    """
    Tests for IsGroupConversation permission.

    Only allows actions on group conversations.
    """

    def test_allows_group_conversation(self, db, group_conversation, owner_user):
        """
        Group conversation is allowed.

        Why it matters: Group-only operations.
        """
        permission = IsGroupConversation()
        request = make_request(owner_user)
        view = MockView()

        result = permission.has_object_permission(request, view, group_conversation)

        assert result is True

    def test_denies_direct_conversation(self, db, direct_conversation, owner_user):
        """
        Direct conversation is denied.

        Why it matters: Some operations only apply to groups.
        """
        permission = IsGroupConversation()
        request = make_request(owner_user)
        view = MockView()

        result = permission.has_object_permission(request, view, direct_conversation)

        assert result is False

    def test_handles_participant_object(
        self, db, group_conversation_with_members, admin_participant
    ):
        """
        Permission works when passed Participant object.

        Why it matters: Some views use Participant as the object.
        """
        permission = IsGroupConversation()
        request = make_request(admin_participant.user)
        view = MockView()

        result = permission.has_object_permission(request, view, admin_participant)

        assert result is True


# =============================================================================
# TestPermissionMatrix
# =============================================================================


class TestPermissionMatrix:
    """
    Integration tests verifying the complete permission matrix.

    Tests the full hierarchy: OWNER > ADMIN > MEMBER

    Matrix:
    | Action               | Owner | Admin | Member | Non-Participant |
    |---------------------|-------|-------|--------|-----------------|
    | View conversation   | Yes   | Yes   | Yes    | No              |
    | Update title        | Yes   | Yes   | No     | No              |
    | Delete conversation | Yes   | No    | No     | No              |
    | Add admin           | Yes   | No    | No     | No              |
    | Add member          | Yes   | Yes   | No     | No              |
    | Remove admin        | Yes   | No    | No     | No              |
    | Remove member       | Yes   | Yes   | No     | No              |
    | Change roles        | Yes   | No    | No     | No              |
    | Transfer ownership  | Yes   | No    | No     | No              |
    | Send message        | Yes   | Yes   | Yes    | No              |
    | Delete own message  | Yes   | Yes   | Yes    | No              |
    | Delete any message  | Yes*  | No    | No     | No              |
    | Leave conversation  | Yes   | Yes   | Yes    | No              |

    * Group owner can delete any message in their group
    """

    @pytest.fixture
    def permission_matrix_scenario(self, db):
        """Create scenario for testing full permission matrix."""
        owner = UserFactory(email_verified=True)
        admin = UserFactory(email_verified=True)
        member = UserFactory(email_verified=True)
        non_participant = UserFactory(email_verified=True)

        conversation = Conversation.objects.create(
            conversation_type=ConversationType.GROUP,
            title="Permission Matrix Test",
            created_by=owner,
            participant_count=3,
        )

        owner_p = Participant.objects.create(
            conversation=conversation,
            user=owner,
            role=ParticipantRole.OWNER,
        )
        admin_p = Participant.objects.create(
            conversation=conversation,
            user=admin,
            role=ParticipantRole.ADMIN,
        )
        member_p = Participant.objects.create(
            conversation=conversation,
            user=member,
            role=ParticipantRole.MEMBER,
        )

        return {
            "conversation": conversation,
            "owner": owner,
            "admin": admin,
            "member": member,
            "non_participant": non_participant,
            "owner_participant": owner_p,
            "admin_participant": admin_p,
            "member_participant": member_p,
        }

    # -------------------------------------------------------------------------
    # View Conversation
    # -------------------------------------------------------------------------

    def test_view_conversation_matrix(self, permission_matrix_scenario):
        """Test view permission for all roles."""
        scenario = permission_matrix_scenario
        conv = scenario["conversation"]
        permission = IsConversationParticipant()
        view = MockView()

        # Owner: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["owner"]), view, conv
            )
            is True
        )

        # Admin: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["admin"]), view, conv
            )
            is True
        )

        # Member: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["member"]), view, conv
            )
            is True
        )

        # Non-participant: No
        assert (
            permission.has_object_permission(
                make_request(scenario["non_participant"]), view, conv
            )
            is False
        )

    # -------------------------------------------------------------------------
    # Update Title (Admin or Owner)
    # -------------------------------------------------------------------------

    def test_update_title_matrix(self, permission_matrix_scenario):
        """Test title update permission for all roles."""
        scenario = permission_matrix_scenario
        conv = scenario["conversation"]
        permission = IsConversationAdminOrOwner()
        view = MockView()

        # Owner: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["owner"]), view, conv
            )
            is True
        )

        # Admin: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["admin"]), view, conv
            )
            is True
        )

        # Member: No
        assert (
            permission.has_object_permission(
                make_request(scenario["member"]), view, conv
            )
            is False
        )

        # Non-participant: No
        assert (
            permission.has_object_permission(
                make_request(scenario["non_participant"]), view, conv
            )
            is False
        )

    # -------------------------------------------------------------------------
    # Delete Conversation (Owner only)
    # -------------------------------------------------------------------------

    def test_delete_conversation_matrix(self, permission_matrix_scenario):
        """Test delete permission for all roles."""
        scenario = permission_matrix_scenario
        conv = scenario["conversation"]
        permission = IsConversationOwner()
        view = MockView()

        # Owner: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["owner"]), view, conv
            )
            is True
        )

        # Admin: No
        assert (
            permission.has_object_permission(
                make_request(scenario["admin"]), view, conv
            )
            is False
        )

        # Member: No
        assert (
            permission.has_object_permission(
                make_request(scenario["member"]), view, conv
            )
            is False
        )

        # Non-participant: No
        assert (
            permission.has_object_permission(
                make_request(scenario["non_participant"]), view, conv
            )
            is False
        )

    # -------------------------------------------------------------------------
    # Add Admin (Owner only)
    # -------------------------------------------------------------------------

    def test_add_admin_matrix(self, permission_matrix_scenario):
        """Test add admin permission for all roles."""
        scenario = permission_matrix_scenario
        conv = scenario["conversation"]
        permission = CanManageParticipants()
        view = MockView()

        def make_add_admin_request(user):
            request = make_request(user, method="POST")
            request._full_data = {"role": ParticipantRole.ADMIN}
            return request

        # Owner: Yes
        assert (
            permission.has_object_permission(
                make_add_admin_request(scenario["owner"]), view, conv
            )
            is True
        )

        # Admin: No
        assert (
            permission.has_object_permission(
                make_add_admin_request(scenario["admin"]), view, conv
            )
            is False
        )

        # Member: No
        assert (
            permission.has_object_permission(
                make_add_admin_request(scenario["member"]), view, conv
            )
            is False
        )

    # -------------------------------------------------------------------------
    # Add Member (Admin or Owner)
    # -------------------------------------------------------------------------

    def test_add_member_matrix(self, permission_matrix_scenario):
        """Test add member permission for all roles."""
        scenario = permission_matrix_scenario
        conv = scenario["conversation"]
        permission = CanManageParticipants()
        view = MockView()

        def make_add_member_request(user):
            request = make_request(user, method="POST")
            request._full_data = {"role": ParticipantRole.MEMBER}
            return request

        # Owner: Yes
        assert (
            permission.has_object_permission(
                make_add_member_request(scenario["owner"]), view, conv
            )
            is True
        )

        # Admin: Yes
        assert (
            permission.has_object_permission(
                make_add_member_request(scenario["admin"]), view, conv
            )
            is True
        )

        # Member: No
        assert (
            permission.has_object_permission(
                make_add_member_request(scenario["member"]), view, conv
            )
            is False
        )

    # -------------------------------------------------------------------------
    # Remove Admin (Owner only)
    # -------------------------------------------------------------------------

    def test_remove_admin_matrix(self, permission_matrix_scenario):
        """Test remove admin permission for all roles."""
        scenario = permission_matrix_scenario
        admin_p = scenario["admin_participant"]
        permission = CanManageParticipants()
        view = MockView()

        # Owner: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["owner"], method="DELETE"), view, admin_p
            )
            is True
        )

        # Admin: No (cannot remove peer admin)
        # Need another admin to test this
        another_admin = UserFactory(email_verified=True)
        Participant.objects.create(
            conversation=scenario["conversation"],
            user=another_admin,
            role=ParticipantRole.ADMIN,
        )
        assert (
            permission.has_object_permission(
                make_request(another_admin, method="DELETE"), view, admin_p
            )
            is False
        )

        # Member: No
        assert (
            permission.has_object_permission(
                make_request(scenario["member"], method="DELETE"), view, admin_p
            )
            is False
        )

    # -------------------------------------------------------------------------
    # Remove Member (Admin or Owner)
    # -------------------------------------------------------------------------

    def test_remove_member_matrix(self, permission_matrix_scenario):
        """Test remove member permission for all roles."""
        scenario = permission_matrix_scenario
        member_p = scenario["member_participant"]
        permission = CanManageParticipants()
        view = MockView()

        # Owner: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["owner"], method="DELETE"), view, member_p
            )
            is True
        )

        # Admin: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["admin"], method="DELETE"), view, member_p
            )
            is True
        )

        # Another member cannot remove member
        another_member = UserFactory(email_verified=True)
        Participant.objects.create(
            conversation=scenario["conversation"],
            user=another_member,
            role=ParticipantRole.MEMBER,
        )
        assert (
            permission.has_object_permission(
                make_request(another_member, method="DELETE"), view, member_p
            )
            is False
        )

    # -------------------------------------------------------------------------
    # Change Roles (Owner only)
    # -------------------------------------------------------------------------

    def test_change_roles_matrix(self, permission_matrix_scenario):
        """Test role change permission for all roles."""
        scenario = permission_matrix_scenario
        member_p = scenario["member_participant"]
        permission = CanManageParticipants()
        view = MockView()

        # Owner: Yes
        assert (
            permission.has_object_permission(
                make_request(scenario["owner"], method="PATCH"), view, member_p
            )
            is True
        )

        # Admin: No
        assert (
            permission.has_object_permission(
                make_request(scenario["admin"], method="PATCH"), view, member_p
            )
            is False
        )

        # Member: No
        another_member = UserFactory(email_verified=True)
        Participant.objects.create(
            conversation=scenario["conversation"],
            user=another_member,
            role=ParticipantRole.MEMBER,
        )
        assert (
            permission.has_object_permission(
                make_request(another_member, method="PATCH"), view, member_p
            )
            is False
        )
