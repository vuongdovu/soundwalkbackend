"""
Comprehensive tests for chat service layer business logic.

This module tests all chat services following TDD principles:
- ConversationService: Conversation lifecycle (create, update, delete)
- ParticipantService: Participant management (add, remove, leave, roles, ownership)
- MessageService: Message operations (send, delete, mark as read)

Test Organization:
    - Each service method has its own test class
    - Each test validates ONE specific behavior
    - Tests use descriptive names following: test_<scenario>_<expected_outcome>

Testing Philosophy:
    Tests focus on observable behavior, not implementation details:
    - ServiceResult success/failure states
    - Database state changes
    - Error codes for specific failure modes
    - System message creation
"""

import json
from datetime import timedelta

from django.utils import timezone

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
from chat.services import ConversationService, MessageService, ParticipantService


# =============================================================================
# TestConversationServiceCreateDirect
# =============================================================================


class TestConversationServiceCreateDirect:
    """
    Tests for ConversationService.create_direct().

    Verifies:
    - Creating new direct conversations
    - Returning existing conversation for same user pair
    - Preventing self-conversations
    - Canonical user ordering
    """

    def test_creates_new_direct_conversation_between_two_users(self, db):
        """
        Successfully creates direct conversation between two different users.

        Why it matters: This is the primary happy path for starting a DM.
        """
        user1 = UserFactory()
        user2 = UserFactory()

        result = ConversationService.create_direct(user1, user2)

        assert result.success is True
        assert result.data.conversation_type == ConversationType.DIRECT
        assert result.data.participant_count == 2
        assert DirectConversationPair.objects.filter(conversation=result.data).exists()

    def test_returns_existing_conversation_for_same_user_pair(self, db):
        """
        Calling with same users returns existing conversation, not a new one.

        Why it matters: Ensures uniqueness of direct conversations.
        Users should always land in the same conversation when DMing.
        """
        user1 = UserFactory()
        user2 = UserFactory()

        # First call creates
        result1 = ConversationService.create_direct(user1, user2)
        # Second call with same users
        result2 = ConversationService.create_direct(user1, user2)

        assert result1.success is True
        assert result2.success is True
        assert result1.data.id == result2.data.id
        # Should still only be one conversation
        assert (
            Conversation.objects.filter(
                conversation_type=ConversationType.DIRECT
            ).count()
            == 1
        )

    def test_returns_existing_regardless_of_user_order(self, db):
        """
        Returns same conversation whether user1,user2 or user2,user1.

        Why it matters: The service canonicalizes order internally.
        Either user initiating should find the same conversation.
        """
        user1 = UserFactory()
        user2 = UserFactory()

        result1 = ConversationService.create_direct(user1, user2)
        result2 = ConversationService.create_direct(user2, user1)  # Reversed

        assert result1.data.id == result2.data.id

    def test_fails_for_same_user_twice(self, db):
        """
        Cannot create direct conversation with yourself.

        Why it matters: Direct conversations are between TWO different people.
        Self-conversations don't make sense.
        """
        user = UserFactory()

        result = ConversationService.create_direct(user, user)

        assert result.success is False
        assert result.error_code == "SAME_USER"

    def test_creates_participants_without_roles(self, db):
        """
        Direct conversation participants have no role (role=None).

        Why it matters: Roles are only for group conversations.
        Direct conversations have equal participants.
        """
        user1 = UserFactory()
        user2 = UserFactory()

        result = ConversationService.create_direct(user1, user2)

        participants = result.data.participants.all()
        assert participants.count() == 2
        for p in participants:
            assert p.role is None

    def test_creates_direct_conversation_pair_with_canonical_order(self, db):
        """
        DirectConversationPair stores users in canonical order (lower ID first).

        Why it matters: Ensures the uniqueness constraint works correctly.
        """
        user1 = UserFactory()
        user2 = UserFactory()

        # Determine expected order
        expected_lower = user1 if user1.id < user2.id else user2
        expected_higher = user2 if user1.id < user2.id else user1

        result = ConversationService.create_direct(user1, user2)

        pair = DirectConversationPair.objects.get(conversation=result.data)
        assert pair.user_lower == expected_lower
        assert pair.user_higher == expected_higher


# =============================================================================
# TestConversationServiceCreateGroup
# =============================================================================


class TestConversationServiceCreateGroup:
    """
    Tests for ConversationService.create_group().

    Verifies:
    - Creating group with title and members
    - Creator becomes owner
    - Initial members become members
    - System message for group creation
    - Title validation
    """

    def test_creates_group_with_title(self, db):
        """
        Successfully creates group conversation with title.

        Why it matters: Groups must have titles for identification.
        """
        creator = UserFactory()

        result = ConversationService.create_group(
            creator=creator,
            title="Test Group",
        )

        assert result.success is True
        assert result.data.conversation_type == ConversationType.GROUP
        assert result.data.title == "Test Group"
        assert result.data.created_by == creator

    def test_creator_becomes_owner(self, db):
        """
        Creator is automatically assigned OWNER role.

        Why it matters: Every group needs an owner for management.
        """
        creator = UserFactory()

        result = ConversationService.create_group(
            creator=creator,
            title="Owner Test",
        )

        owner_participant = result.data.participants.get(user=creator)
        assert owner_participant.role == ParticipantRole.OWNER

    def test_adds_initial_members(self, db):
        """
        Initial members are added with MEMBER role.

        Why it matters: Creators often want to add people when creating groups.
        """
        creator = UserFactory()
        member1 = UserFactory()
        member2 = UserFactory()

        result = ConversationService.create_group(
            creator=creator,
            title="Members Test",
            initial_members=[member1, member2],
        )

        assert result.data.participant_count == 3
        member1_participant = result.data.participants.get(user=member1)
        member2_participant = result.data.participants.get(user=member2)
        assert member1_participant.role == ParticipantRole.MEMBER
        assert member2_participant.role == ParticipantRole.MEMBER

    def test_creator_excluded_from_initial_members(self, db):
        """
        If creator is in initial_members list, they're not added twice.

        Why it matters: Prevents duplicate participant records for the creator.
        """
        creator = UserFactory()

        result = ConversationService.create_group(
            creator=creator,
            title="Dedup Test",
            initial_members=[creator],  # Creator in list
        )

        # Should only have 1 participant (the owner), not 2
        assert result.data.participant_count == 1
        assert result.data.participants.filter(user=creator).count() == 1

    def test_creates_system_message_for_group_creation(self, db):
        """
        System message is created when group is created.

        Why it matters: Provides context for when the group started.
        """
        creator = UserFactory()

        result = ConversationService.create_group(
            creator=creator,
            title="System Msg Test",
        )

        system_msg = result.data.messages.filter(
            message_type=MessageType.SYSTEM
        ).first()
        assert system_msg is not None
        data = json.loads(system_msg.content)
        assert data["event"] == SystemMessageEvent.GROUP_CREATED

    def test_fails_with_empty_title(self, db):
        """
        Group creation fails without a title.

        Why it matters: Groups need titles for display and identification.
        """
        creator = UserFactory()

        result = ConversationService.create_group(
            creator=creator,
            title="",
        )

        assert result.success is False
        assert result.error_code == "TITLE_REQUIRED"

    def test_fails_with_whitespace_only_title(self, db):
        """
        Title with only whitespace is rejected.

        Why it matters: Whitespace-only titles are effectively empty.
        """
        creator = UserFactory()

        result = ConversationService.create_group(
            creator=creator,
            title="   ",
        )

        assert result.success is False
        assert result.error_code == "TITLE_REQUIRED"


# =============================================================================
# TestConversationServiceUpdateTitle
# =============================================================================


class TestConversationServiceUpdateTitle:
    """
    Tests for ConversationService.update_title().

    Verifies:
    - Title updates by authorized users
    - Permission checks (admin/owner only)
    - System message creation
    - Direct conversation restriction
    """

    def test_owner_can_update_title(self, db, group_conversation, owner_user):
        """
        Owner can successfully update group title.

        Why it matters: Owners have full control over group settings.
        """
        result = ConversationService.update_title(
            conversation=group_conversation,
            user=owner_user,
            new_title="Updated Title",
        )

        assert result.success is True
        assert result.data.title == "Updated Title"

    def test_admin_can_update_title(
        self, db, group_conversation_with_members, admin_user
    ):
        """
        Admin can successfully update group title.

        Why it matters: Admins have management permissions.
        """
        result = ConversationService.update_title(
            conversation=group_conversation_with_members,
            user=admin_user,
            new_title="Admin Updated",
        )

        assert result.success is True
        assert result.data.title == "Admin Updated"

    def test_member_cannot_update_title(
        self, db, group_conversation_with_members, member_user
    ):
        """
        Regular member cannot update group title.

        Why it matters: Members have limited permissions.
        """
        result = ConversationService.update_title(
            conversation=group_conversation_with_members,
            user=member_user,
            new_title="Member Attempt",
        )

        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    def test_creates_system_message_on_title_change(
        self, db, group_conversation, owner_user
    ):
        """
        System message records title change.

        Why it matters: Audit trail for group changes.
        """
        old_title = group_conversation.title

        ConversationService.update_title(
            conversation=group_conversation,
            user=owner_user,
            new_title="New Title",
        )

        # Find the title changed message
        system_msgs = group_conversation.messages.filter(
            message_type=MessageType.SYSTEM
        )
        title_msg = None
        for msg in system_msgs:
            data = json.loads(msg.content)
            if data.get("event") == SystemMessageEvent.TITLE_CHANGED:
                title_msg = msg
                break

        assert title_msg is not None
        data = json.loads(title_msg.content)
        assert data["data"]["old_title"] == old_title
        assert data["data"]["new_title"] == "New Title"

    def test_fails_on_direct_conversation(self, db, direct_conversation, owner_user):
        """
        Cannot set title on direct conversations.

        Why it matters: Direct conversations are identified by participants,
        not titles.
        """
        result = ConversationService.update_title(
            conversation=direct_conversation,
            user=owner_user,
            new_title="DM Title",
        )

        assert result.success is False
        assert result.error_code == "NOT_GROUP"

    def test_fails_for_non_participant(
        self, db, group_conversation, non_participant_user
    ):
        """
        Non-participants cannot update title.

        Why it matters: Only conversation members can modify it.
        """
        result = ConversationService.update_title(
            conversation=group_conversation,
            user=non_participant_user,
            new_title="Outsider Attempt",
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"


# =============================================================================
# TestConversationServiceDelete
# =============================================================================


class TestConversationServiceDeleteConversation:
    """
    Tests for ConversationService.delete_conversation().

    Verifies:
    - Soft delete by owner
    - Permission restrictions
    - Direct conversation restriction
    """

    def test_owner_can_delete_group(self, db, group_conversation, owner_user):
        """
        Owner can soft delete group conversation.

        Why it matters: Owners have full control.
        """
        result = ConversationService.delete_conversation(
            conversation=group_conversation,
            user=owner_user,
        )

        assert result.success is True
        group_conversation.refresh_from_db()
        assert group_conversation.is_deleted is True
        assert group_conversation.deleted_at is not None

    def test_admin_cannot_delete_group(
        self, db, group_conversation_with_members, admin_user
    ):
        """
        Admin cannot delete group (owner only).

        Why it matters: Delete is a destructive operation reserved for owners.
        """
        result = ConversationService.delete_conversation(
            conversation=group_conversation_with_members,
            user=admin_user,
        )

        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    def test_cannot_delete_direct_conversation(
        self, db, direct_conversation, owner_user
    ):
        """
        Direct conversations cannot be deleted, only left.

        Why it matters: Direct conversations are shared resources between
        two users. Either can leave, but deletion requires both to leave.
        """
        result = ConversationService.delete_conversation(
            conversation=direct_conversation,
            user=owner_user,
        )

        assert result.success is False
        assert result.error_code == "NOT_GROUP"


# =============================================================================
# TestParticipantServiceAddParticipant
# =============================================================================


class TestParticipantServiceAddParticipant:
    """
    Tests for ParticipantService.add_participant().

    Verifies:
    - Adding members and admins
    - Permission hierarchy (owner can add admins, admin can add members)
    - Duplicate prevention
    - Direct conversation restriction
    """

    def test_owner_can_add_member(self, db, group_conversation, owner_user):
        """
        Owner can add new member to group.

        Why it matters: Basic member addition functionality.
        """
        new_user = UserFactory()

        result = ParticipantService.add_participant(
            conversation=group_conversation,
            user_to_add=new_user,
            added_by=owner_user,
            role=ParticipantRole.MEMBER,
        )

        assert result.success is True
        assert result.data.user == new_user
        assert result.data.role == ParticipantRole.MEMBER

    def test_owner_can_add_admin(self, db, group_conversation, owner_user):
        """
        Owner can add new admin to group.

        Why it matters: Only owners can grant admin privileges.
        """
        new_user = UserFactory()

        result = ParticipantService.add_participant(
            conversation=group_conversation,
            user_to_add=new_user,
            added_by=owner_user,
            role=ParticipantRole.ADMIN,
        )

        assert result.success is True
        assert result.data.role == ParticipantRole.ADMIN

    def test_admin_can_add_member(
        self, db, group_conversation_with_members, admin_user
    ):
        """
        Admin can add new member to group.

        Why it matters: Admins have member management permissions.
        """
        new_user = UserFactory()

        result = ParticipantService.add_participant(
            conversation=group_conversation_with_members,
            user_to_add=new_user,
            added_by=admin_user,
            role=ParticipantRole.MEMBER,
        )

        assert result.success is True

    def test_admin_cannot_add_admin(
        self, db, group_conversation_with_members, admin_user
    ):
        """
        Admin cannot grant admin role (owner only).

        Why it matters: Privilege escalation prevention.
        """
        new_user = UserFactory()

        result = ParticipantService.add_participant(
            conversation=group_conversation_with_members,
            user_to_add=new_user,
            added_by=admin_user,
            role=ParticipantRole.ADMIN,
        )

        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    def test_member_cannot_add_anyone(
        self, db, group_conversation_with_members, member_user
    ):
        """
        Regular members cannot add participants.

        Why it matters: Members have limited permissions.
        """
        new_user = UserFactory()

        result = ParticipantService.add_participant(
            conversation=group_conversation_with_members,
            user_to_add=new_user,
            added_by=member_user,
        )

        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    def test_cannot_add_owner_role(self, db, group_conversation, owner_user):
        """
        Cannot add someone directly as owner.

        Why it matters: Ownership is transferred, not granted.
        """
        new_user = UserFactory()

        result = ParticipantService.add_participant(
            conversation=group_conversation,
            user_to_add=new_user,
            added_by=owner_user,
            role=ParticipantRole.OWNER,
        )

        assert result.success is False
        assert result.error_code == "CANNOT_ADD_OWNER"

    def test_cannot_add_existing_participant(
        self, db, group_conversation_with_members, owner_user, member_user
    ):
        """
        Cannot add user who is already an active participant.

        Why it matters: Prevents duplicate memberships.
        """
        result = ParticipantService.add_participant(
            conversation=group_conversation_with_members,
            user_to_add=member_user,
            added_by=owner_user,
        )

        assert result.success is False
        assert result.error_code == "ALREADY_PARTICIPANT"

    def test_cannot_add_to_direct_conversation(
        self, db, direct_conversation, owner_user
    ):
        """
        Cannot add participants to direct conversations.

        Why it matters: Direct conversations are always exactly 2 people.
        """
        new_user = UserFactory()

        result = ParticipantService.add_participant(
            conversation=direct_conversation,
            user_to_add=new_user,
            added_by=owner_user,
        )

        assert result.success is False
        assert result.error_code == "NOT_GROUP"

    def test_updates_participant_count(self, db, group_conversation, owner_user):
        """
        Adding participant increments participant_count.

        Why it matters: Cached count should stay accurate.
        """
        initial_count = group_conversation.participant_count
        new_user = UserFactory()

        ParticipantService.add_participant(
            conversation=group_conversation,
            user_to_add=new_user,
            added_by=owner_user,
        )

        group_conversation.refresh_from_db()
        assert group_conversation.participant_count == initial_count + 1

    def test_creates_system_message(self, db, group_conversation, owner_user):
        """
        System message records participant addition.

        Why it matters: Audit trail for membership changes.
        """
        new_user = UserFactory()

        ParticipantService.add_participant(
            conversation=group_conversation,
            user_to_add=new_user,
            added_by=owner_user,
        )

        system_msgs = group_conversation.messages.filter(
            message_type=MessageType.SYSTEM
        )
        added_msg = None
        for msg in system_msgs:
            data = json.loads(msg.content)
            if data.get("event") == SystemMessageEvent.PARTICIPANT_ADDED:
                if data["data"].get("user_id") == str(new_user.id):
                    added_msg = msg
                    break

        assert added_msg is not None


# =============================================================================
# TestParticipantServiceRemoveParticipant
# =============================================================================


class TestParticipantServiceRemoveParticipant:
    """
    Tests for ParticipantService.remove_participant().

    Verifies:
    - Permission hierarchy for removal
    - Cannot remove owner
    - Cannot remove self (use leave instead)
    """

    def test_owner_can_remove_member(
        self, db, group_conversation_with_members, owner_user, member_user
    ):
        """
        Owner can remove a member from the group.

        Why it matters: Owners have full control over membership.
        """
        result = ParticipantService.remove_participant(
            conversation=group_conversation_with_members,
            user_to_remove=member_user,
            removed_by=owner_user,
        )

        assert result.success is True
        participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
        )
        assert participant.left_at is not None
        assert participant.left_voluntarily is False
        assert participant.removed_by == owner_user

    def test_owner_can_remove_admin(
        self, db, group_conversation_with_members, owner_user, admin_user
    ):
        """
        Owner can remove an admin from the group.

        Why it matters: Owner has authority over all roles.
        """
        result = ParticipantService.remove_participant(
            conversation=group_conversation_with_members,
            user_to_remove=admin_user,
            removed_by=owner_user,
        )

        assert result.success is True

    def test_admin_can_remove_member(
        self, db, group_conversation_with_members, admin_user, member_user
    ):
        """
        Admin can remove a member from the group.

        Why it matters: Admins can manage regular members.
        """
        result = ParticipantService.remove_participant(
            conversation=group_conversation_with_members,
            user_to_remove=member_user,
            removed_by=admin_user,
        )

        assert result.success is True

    def test_admin_cannot_remove_admin(self, db):
        """
        Admin cannot remove another admin.

        Why it matters: Admins are peers - only owner can remove them.
        """
        owner = UserFactory()
        admin1 = UserFactory()
        admin2 = UserFactory()

        conversation = Conversation.objects.create(
            conversation_type=ConversationType.GROUP,
            title="Admin Test",
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
        Participant.objects.create(
            conversation=conversation,
            user=admin2,
            role=ParticipantRole.ADMIN,
        )

        result = ParticipantService.remove_participant(
            conversation=conversation,
            user_to_remove=admin2,
            removed_by=admin1,
        )

        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    def test_member_cannot_remove_anyone(
        self, db, group_conversation_with_members, member_user, admin_user
    ):
        """
        Regular member cannot remove other participants.

        Why it matters: Members have no management permissions.
        """
        result = ParticipantService.remove_participant(
            conversation=group_conversation_with_members,
            user_to_remove=admin_user,
            removed_by=member_user,
        )

        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    def test_cannot_remove_owner(
        self, db, group_conversation_with_members, admin_user, owner_user
    ):
        """
        Cannot remove the owner (must transfer ownership first).

        Why it matters: Groups need an owner at all times.
        """
        result = ParticipantService.remove_participant(
            conversation=group_conversation_with_members,
            user_to_remove=owner_user,
            removed_by=admin_user,
        )

        assert result.success is False
        assert result.error_code == "CANNOT_REMOVE_OWNER"

    def test_cannot_remove_self(self, db, group_conversation_with_members, owner_user):
        """
        Cannot remove yourself via remove_participant (use leave).

        Why it matters: Self-removal has different semantics (voluntary leave).
        """
        result = ParticipantService.remove_participant(
            conversation=group_conversation_with_members,
            user_to_remove=owner_user,
            removed_by=owner_user,
        )

        assert result.success is False
        assert result.error_code == "CANNOT_REMOVE_SELF"


# =============================================================================
# TestParticipantServiceLeave
# =============================================================================


class TestParticipantServiceLeave:
    """
    Tests for ParticipantService.leave().

    Verifies:
    - Voluntary leave behavior
    - Ownership transfer on owner departure
    - Conversation deletion when empty
    - Direct conversation behavior
    """

    def test_member_can_leave_group(
        self, db, group_conversation_with_members, member_user
    ):
        """
        Member can voluntarily leave group.

        Why it matters: Basic leave functionality.
        """
        result = ParticipantService.leave(
            conversation=group_conversation_with_members,
            user=member_user,
        )

        assert result.success is True
        participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
        )
        assert participant.left_at is not None
        assert participant.left_voluntarily is True

    def test_owner_leaving_transfers_to_oldest_admin(
        self, db, ownership_transfer_scenario
    ):
        """
        When owner leaves, ownership transfers to oldest admin.

        Why it matters: Groups need continuous ownership.
        Admin has priority over member for transfer.
        """
        scenario = ownership_transfer_scenario
        owner_client = scenario["owner"]
        admin = scenario["admin"]

        result = ParticipantService.leave(
            conversation=scenario["conversation"],
            user=owner_client,
        )

        assert result.success is True
        # Admin should now be owner
        admin_participant = Participant.objects.get(
            conversation=scenario["conversation"],
            user=admin,
            left_at__isnull=True,
        )
        assert admin_participant.role == ParticipantRole.OWNER

    def test_owner_leaving_transfers_to_oldest_member_if_no_admin(self, db):
        """
        When owner leaves and no admins exist, ownership goes to oldest member.

        Why it matters: Fallback ownership transfer logic.
        """
        owner = UserFactory()
        member1 = UserFactory()
        member2 = UserFactory()

        conversation = Conversation.objects.create(
            conversation_type=ConversationType.GROUP,
            title="No Admin Test",
            created_by=owner,
            participant_count=3,
        )
        Participant.objects.create(
            conversation=conversation,
            user=owner,
            role=ParticipantRole.OWNER,
        )
        # member1 joins first
        Participant.objects.create(
            conversation=conversation,
            user=member1,
            role=ParticipantRole.MEMBER,
        )
        Participant.objects.create(
            conversation=conversation,
            user=member2,
            role=ParticipantRole.MEMBER,
        )

        ParticipantService.leave(conversation=conversation, user=owner)

        # member1 (oldest) should be owner
        member1_participant = Participant.objects.get(
            conversation=conversation,
            user=member1,
            left_at__isnull=True,
        )
        assert member1_participant.role == ParticipantRole.OWNER

    def test_conversation_deleted_when_last_member_leaves(self, db):
        """
        Group is soft-deleted when last participant leaves.

        Why it matters: Empty groups should be cleaned up.
        """
        owner = UserFactory()
        conversation = Conversation.objects.create(
            conversation_type=ConversationType.GROUP,
            title="Solo Group",
            created_by=owner,
            participant_count=1,
        )
        Participant.objects.create(
            conversation=conversation,
            user=owner,
            role=ParticipantRole.OWNER,
        )

        ParticipantService.leave(conversation=conversation, user=owner)

        conversation.refresh_from_db()
        assert conversation.is_deleted is True

    def test_direct_conversation_not_deleted_until_both_leave(
        self, db, direct_conversation, owner_user, other_user
    ):
        """
        Direct conversation is only deleted when both users have left.

        Why it matters: Either user might want to access history.
        """
        # First user leaves
        ParticipantService.leave(conversation=direct_conversation, user=owner_user)

        direct_conversation.refresh_from_db()
        assert direct_conversation.is_deleted is False

        # Second user leaves
        ParticipantService.leave(conversation=direct_conversation, user=other_user)

        direct_conversation.refresh_from_db()
        assert direct_conversation.is_deleted is True

    def test_non_participant_cannot_leave(
        self, db, group_conversation, non_participant_user
    ):
        """
        Non-participants get error when trying to leave.

        Why it matters: Can't leave what you're not part of.
        """
        result = ParticipantService.leave(
            conversation=group_conversation,
            user=non_participant_user,
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"


# =============================================================================
# TestParticipantServiceChangeRole
# =============================================================================


class TestParticipantServiceChangeRole:
    """
    Tests for ParticipantService.change_role().

    Verifies:
    - Only owner can change roles
    - Valid role transitions
    - Cannot change owner's role
    """

    def test_owner_can_promote_member_to_admin(
        self, db, group_conversation_with_members, owner_user, member_user
    ):
        """
        Owner can promote member to admin.

        Why it matters: Role management for group administration.
        """
        result = ParticipantService.change_role(
            conversation=group_conversation_with_members,
            user_to_change=member_user,
            new_role=ParticipantRole.ADMIN,
            changed_by=owner_user,
        )

        assert result.success is True
        assert result.data.role == ParticipantRole.ADMIN

    def test_owner_can_demote_admin_to_member(
        self, db, group_conversation_with_members, owner_user, admin_user
    ):
        """
        Owner can demote admin to member.

        Why it matters: Owner should be able to revoke admin privileges.
        """
        result = ParticipantService.change_role(
            conversation=group_conversation_with_members,
            user_to_change=admin_user,
            new_role=ParticipantRole.MEMBER,
            changed_by=owner_user,
        )

        assert result.success is True
        assert result.data.role == ParticipantRole.MEMBER

    def test_admin_cannot_change_roles(
        self, db, group_conversation_with_members, admin_user, member_user
    ):
        """
        Admin cannot change participant roles.

        Why it matters: Role management is owner-only.
        """
        result = ParticipantService.change_role(
            conversation=group_conversation_with_members,
            user_to_change=member_user,
            new_role=ParticipantRole.ADMIN,
            changed_by=admin_user,
        )

        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    def test_cannot_change_to_owner_role(
        self, db, group_conversation_with_members, owner_user, member_user
    ):
        """
        Cannot change someone to OWNER role (use transfer_ownership).

        Why it matters: Ownership transfer is a distinct operation.
        """
        result = ParticipantService.change_role(
            conversation=group_conversation_with_members,
            user_to_change=member_user,
            new_role=ParticipantRole.OWNER,
            changed_by=owner_user,
        )

        assert result.success is False
        assert result.error_code == "INVALID_ROLE"

    def test_cannot_change_owner_role(
        self, db, group_conversation_with_members, owner_user, admin_user
    ):
        """
        Cannot change the owner's role to something else.

        Why it matters: Owner must transfer ownership, not demote themselves.
        """
        result = ParticipantService.change_role(
            conversation=group_conversation_with_members,
            user_to_change=owner_user,
            new_role=ParticipantRole.MEMBER,
            changed_by=owner_user,
        )

        assert result.success is False
        assert result.error_code == "CANNOT_CHANGE_OWNER_ROLE"


# =============================================================================
# TestParticipantServiceTransferOwnership
# =============================================================================


class TestParticipantServiceTransferOwnership:
    """
    Tests for ParticipantService.transfer_ownership().

    Verifies:
    - Manual ownership transfer
    - Old owner becomes admin
    - New owner must be participant
    """

    def test_owner_can_transfer_to_admin(
        self, db, group_conversation_with_members, owner_user, admin_user
    ):
        """
        Owner can transfer ownership to an admin.

        Why it matters: Orderly succession of group ownership.
        """
        result = ParticipantService.transfer_ownership(
            conversation=group_conversation_with_members,
            new_owner=admin_user,
            current_owner=owner_user,
        )

        assert result.success is True

        # New owner should be owner
        new_owner_participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=admin_user,
            left_at__isnull=True,
        )
        assert new_owner_participant.role == ParticipantRole.OWNER

        # Old owner should be admin
        old_owner_participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=owner_user,
            left_at__isnull=True,
        )
        assert old_owner_participant.role == ParticipantRole.ADMIN

    def test_owner_can_transfer_to_member(
        self, db, group_conversation_with_members, owner_user, member_user
    ):
        """
        Owner can transfer ownership to a member.

        Why it matters: Any active participant can receive ownership.
        """
        result = ParticipantService.transfer_ownership(
            conversation=group_conversation_with_members,
            new_owner=member_user,
            current_owner=owner_user,
        )

        assert result.success is True
        new_owner_participant = Participant.objects.get(
            conversation=group_conversation_with_members,
            user=member_user,
            left_at__isnull=True,
        )
        assert new_owner_participant.role == ParticipantRole.OWNER

    def test_cannot_transfer_to_non_participant(
        self, db, group_conversation, owner_user, non_participant_user
    ):
        """
        Cannot transfer ownership to someone not in the group.

        Why it matters: New owner must be an existing participant.
        """
        result = ParticipantService.transfer_ownership(
            conversation=group_conversation,
            new_owner=non_participant_user,
            current_owner=owner_user,
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"

    def test_cannot_transfer_to_self(self, db, group_conversation, owner_user):
        """
        Cannot transfer ownership to yourself.

        Why it matters: No-op that would be confusing.
        """
        result = ParticipantService.transfer_ownership(
            conversation=group_conversation,
            new_owner=owner_user,
            current_owner=owner_user,
        )

        assert result.success is False
        assert result.error_code == "SAME_USER"

    def test_non_owner_cannot_transfer(
        self, db, group_conversation_with_members, admin_user, member_user
    ):
        """
        Only current owner can transfer ownership.

        Why it matters: Prevents unauthorized ownership changes.
        """
        result = ParticipantService.transfer_ownership(
            conversation=group_conversation_with_members,
            new_owner=member_user,
            current_owner=admin_user,
        )

        assert result.success is False
        assert result.error_code == "NOT_OWNER"


# =============================================================================
# TestMessageServiceSendMessage
# =============================================================================


class TestMessageServiceSendMessage:
    """
    Tests for MessageService.send_message().

    Verifies:
    - Basic message sending
    - Threading (reply to message)
    - Single-level threading (reply to reply goes to root)
    - Participant validation
    """

    def test_sends_text_message(self, db, group_conversation, owner_user):
        """
        Participant can send a text message.

        Why it matters: Basic messaging functionality.
        """
        result = MessageService.send_message(
            conversation=group_conversation,
            sender=owner_user,
            content="Hello world!",
        )

        assert result.success is True
        assert result.data.content == "Hello world!"
        assert result.data.message_type == MessageType.TEXT
        assert result.data.sender == owner_user

    def test_updates_conversation_last_message_at(
        self, db, group_conversation, owner_user
    ):
        """
        Sending message updates conversation's last_message_at.

        Why it matters: Used for sorting conversation lists.
        """
        MessageService.send_message(
            conversation=group_conversation,
            sender=owner_user,
            content="Test message",
        )

        group_conversation.refresh_from_db()
        assert group_conversation.last_message_at is not None

    def test_creates_reply_to_message(
        self, db, group_conversation, owner_user, member_user
    ):
        """
        Can send a reply to an existing message.

        Why it matters: Threading support for conversations.
        """
        # Add member to conversation
        Participant.objects.create(
            conversation=group_conversation,
            user=member_user,
            role=ParticipantRole.MEMBER,
        )

        # Create root message
        root_result = MessageService.send_message(
            conversation=group_conversation,
            sender=owner_user,
            content="Root message",
        )
        root = root_result.data

        # Create reply
        reply_result = MessageService.send_message(
            conversation=group_conversation,
            sender=member_user,
            content="Reply message",
            parent_message_id=root.id,
        )

        assert reply_result.success is True
        assert reply_result.data.parent_message == root

    def test_reply_to_reply_references_root(self, db, threading_scenario):
        """
        Replying to a reply references the root message (single-level threading).

        Why it matters: Prevents deeply nested threads, keeping UI simple.
        """
        scenario = threading_scenario
        root = scenario["root"]
        reply = scenario["reply"]

        # Reply to the reply
        result = MessageService.send_message(
            conversation=scenario["conversation"],
            sender=scenario["owner"],
            content="Reply to reply",
            parent_message_id=reply.id,
        )

        # Should reference root, not the reply
        assert result.success is True
        assert result.data.parent_message == root
        assert result.data.parent_message != reply

    def test_increments_reply_count_on_parent(self, db, group_conversation, owner_user):
        """
        Sending a reply increments parent's reply_count.

        Why it matters: Cached count for display without extra queries.
        """
        # Create root message
        root_result = MessageService.send_message(
            conversation=group_conversation,
            sender=owner_user,
            content="Root",
        )
        root = root_result.data
        assert root.reply_count == 0

        # Send reply
        MessageService.send_message(
            conversation=group_conversation,
            sender=owner_user,
            content="Reply",
            parent_message_id=root.id,
        )

        root.refresh_from_db()
        assert root.reply_count == 1

    def test_non_participant_cannot_send(
        self, db, group_conversation, non_participant_user
    ):
        """
        Non-participants cannot send messages.

        Why it matters: Privacy - only members can contribute.
        """
        result = MessageService.send_message(
            conversation=group_conversation,
            sender=non_participant_user,
            content="Unauthorized message",
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"

    def test_cannot_send_empty_message(self, db, group_conversation, owner_user):
        """
        Empty message content is rejected.

        Why it matters: Messages should have content.
        """
        result = MessageService.send_message(
            conversation=group_conversation,
            sender=owner_user,
            content="",
        )

        assert result.success is False
        assert result.error_code == "EMPTY_CONTENT"

    def test_cannot_send_to_deleted_conversation(
        self, db, deleted_conversation, owner_user
    ):
        """
        Cannot send messages to deleted conversations.

        Why it matters: Deleted conversations are archived.
        """
        result = MessageService.send_message(
            conversation=deleted_conversation,
            sender=owner_user,
            content="Test",
        )

        assert result.success is False
        assert result.error_code == "CONVERSATION_DELETED"

    def test_invalid_parent_message_fails(self, db, group_conversation, owner_user):
        """
        Replying to non-existent message fails.

        Why it matters: Data integrity.
        """
        result = MessageService.send_message(
            conversation=group_conversation,
            sender=owner_user,
            content="Reply to nothing",
            parent_message_id=99999,
        )

        assert result.success is False
        assert result.error_code == "INVALID_PARENT"


# =============================================================================
# TestMessageServiceDeleteMessage
# =============================================================================


class TestMessageServiceDeleteMessage:
    """
    Tests for MessageService.delete_message().

    Verifies:
    - Soft delete behavior
    - Permission checks
    - System message protection
    """

    def test_sender_can_delete_own_message(self, db, text_message, owner_user):
        """
        Users can delete their own messages.

        Why it matters: Basic message management.
        """
        result = MessageService.delete_message(
            message=text_message,
            user=owner_user,
        )

        assert result.success is True
        text_message.refresh_from_db()
        assert text_message.is_deleted is True
        assert text_message.deleted_at is not None

    def test_soft_delete_preserves_original_content(self, db, text_message, owner_user):
        """
        Soft delete preserves content in database.

        Why it matters: Audit trail and potential recovery.
        """
        original_content = text_message.content

        MessageService.delete_message(message=text_message, user=owner_user)

        text_message.refresh_from_db()
        assert text_message.content == original_content
        assert text_message.get_display_content() == "[Message deleted]"

    def test_group_owner_can_delete_any_message(
        self, db, group_conversation_with_members, owner_user, member_user
    ):
        """
        Group owner can delete any message in the group.

        Why it matters: Moderation capability for group owners.
        """
        # Create message from member
        message = Message.objects.create(
            conversation=group_conversation_with_members,
            sender=member_user,
            message_type=MessageType.TEXT,
            content="Member message",
        )

        result = MessageService.delete_message(message=message, user=owner_user)

        assert result.success is True
        message.refresh_from_db()
        assert message.is_deleted is True

    def test_member_cannot_delete_others_message(
        self, db, group_conversation_with_members, owner_user, member_user
    ):
        """
        Members cannot delete other users' messages.

        Why it matters: Privacy and control over own content.
        """
        # Create message from owner
        message = Message.objects.create(
            conversation=group_conversation_with_members,
            sender=owner_user,
            message_type=MessageType.TEXT,
            content="Owner message",
        )

        result = MessageService.delete_message(message=message, user=member_user)

        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    def test_cannot_delete_system_message(self, db, system_message, owner_user):
        """
        System messages cannot be deleted.

        Why it matters: System messages are audit trail.
        """
        result = MessageService.delete_message(message=system_message, user=owner_user)

        assert result.success is False
        assert result.error_code == "SYSTEM_MESSAGE"

    def test_cannot_delete_already_deleted_message(
        self, db, deleted_message, owner_user
    ):
        """
        Already deleted messages return error.

        Why it matters: Idempotency and clear error handling.
        """
        result = MessageService.delete_message(message=deleted_message, user=owner_user)

        assert result.success is False
        assert result.error_code == "ALREADY_DELETED"


# =============================================================================
# TestMessageServiceMarkAsRead
# =============================================================================


class TestMessageServiceMarkAsRead:
    """
    Tests for MessageService.mark_as_read().

    Verifies:
    - Updates last_read_at timestamp
    - Requires active participation
    """

    def test_marks_conversation_as_read(self, db, group_conversation, owner_user):
        """
        Successfully updates last_read_at for participant.

        Why it matters: Tracks what the user has seen.
        """
        result = MessageService.mark_as_read(
            conversation=group_conversation,
            user=owner_user,
        )

        assert result.success is True
        participant = Participant.objects.get(
            conversation=group_conversation,
            user=owner_user,
        )
        assert participant.last_read_at is not None

    def test_non_participant_cannot_mark_as_read(
        self, db, group_conversation, non_participant_user
    ):
        """
        Non-participants cannot mark conversation as read.

        Why it matters: Only participants have read status.
        """
        result = MessageService.mark_as_read(
            conversation=group_conversation,
            user=non_participant_user,
        )

        assert result.success is False
        assert result.error_code == "NOT_PARTICIPANT"


# =============================================================================
# TestMessageServiceGetUnreadCount
# =============================================================================


class TestMessageServiceGetUnreadCount:
    """
    Tests for MessageService.get_unread_count().

    Verifies:
    - Counts messages after last_read_at
    - Excludes own messages
    - Returns 0 for non-participants
    """

    def test_counts_unread_messages(self, db, conversation_with_messages, owner_user):
        """
        Returns count of messages after last_read_at.

        Why it matters: Unread badge/indicator functionality.
        """
        conv = conversation_with_messages["conversation"]

        # Mark as read at a point in time
        participant = Participant.objects.get(conversation=conv, user=owner_user)
        participant.last_read_at = timezone.now() - timedelta(hours=1)
        participant.save()

        count = MessageService.get_unread_count(conversation=conv, user=owner_user)

        # Should count messages from other users created after last_read_at
        assert count > 0

    def test_excludes_own_messages(self, db, group_conversation, owner_user):
        """
        Own messages are not counted as unread.

        Why it matters: Users don't need to be notified of their own messages.
        """
        # Send messages as owner
        MessageService.send_message(
            conversation=group_conversation,
            sender=owner_user,
            content="My message 1",
        )
        MessageService.send_message(
            conversation=group_conversation,
            sender=owner_user,
            content="My message 2",
        )

        count = MessageService.get_unread_count(
            conversation=group_conversation,
            user=owner_user,
        )

        assert count == 0

    def test_returns_zero_for_non_participant(
        self, db, group_conversation, non_participant_user
    ):
        """
        Non-participants get 0 unread count.

        Why it matters: Graceful handling of invalid requests.
        """
        count = MessageService.get_unread_count(
            conversation=group_conversation,
            user=non_participant_user,
        )

        assert count == 0
