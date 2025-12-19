"""
Chat system service layer.

This module provides the business logic for the chat system, encapsulating
all operations on conversations, participants, and messages.

Services:
    ConversationService: Conversation lifecycle (create, update, delete)
    ParticipantService: Participant management (add, remove, leave, roles, ownership)
    MessageService: Message operations (send, delete, mark as read)

Design Principles:
    - Services are stateless (use class methods)
    - Expected failures return ServiceResult.failure()
    - Unexpected failures raise exceptions
    - All database operations use transactions where appropriate
    - System messages are generated for significant events

Usage:
    from chat.services import ConversationService, ParticipantService, MessageService

    # Create a direct conversation
    result = ConversationService.create_direct(user1, user2)
    if result.success:
        conversation = result.data

    # Create a group conversation
    result = ConversationService.create_group(
        creator=user,
        title="Project Team",
        initial_members=[user2, user3]
    )

    # Send a message
    result = MessageService.send_message(
        conversation=conversation,
        sender=user,
        content="Hello everyone!"
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.services import BaseService, ServiceResult

from chat.constants import MESSAGE_CONFIG, PRESENCE_CONFIG, REACTION_CONFIG
from chat.models import (
    Conversation,
    ConversationType,
    DirectConversationPair,
    Message,
    MessageEditHistory,
    MessageReaction,
    MessageType,
    Participant,
    ParticipantRole,
    SystemMessageEvent,
)

if TYPE_CHECKING:
    from authentication.models import User

logger = logging.getLogger(__name__)


class ConversationService(BaseService):
    """
    Service for conversation lifecycle operations.

    Methods:
        create_direct: Create or retrieve direct conversation between two users
        create_group: Create a new group conversation
        update_title: Update group conversation title
        delete_conversation: Soft delete a conversation
        get_user_conversations: List user's active conversations
    """

    @classmethod
    def create_direct(
        cls,
        user1: User,
        user2: User,
    ) -> ServiceResult[Conversation]:
        """
        Create or retrieve a direct conversation between two users.

        Direct conversations are unique per user pair. If a conversation
        already exists between the two users, it is returned instead of
        creating a duplicate.

        Implementation:
            1. Validate users are different
            2. Canonicalize order (lower user_id first)
            3. Look up existing DirectConversationPair
            4. If found, return existing conversation
            5. If not found, create new conversation within transaction

        Args:
            user1: First participant
            user2: Second participant

        Returns:
            ServiceResult with Conversation (existing or new)
            - success=True: data contains Conversation
            - success=False: error describes failure

        Error codes:
            SAME_USER: Cannot create direct conversation with yourself
        """
        # Validate users are different
        if user1.id == user2.id:
            return ServiceResult.failure(
                "Cannot create a direct conversation with yourself",
                error_code="SAME_USER",
            )

        # Canonicalize order for uniqueness lookup
        user_lower, user_higher = (
            (user1, user2) if user1.id < user2.id else (user2, user1)
        )

        # Check for existing conversation
        try:
            existing_pair = DirectConversationPair.objects.select_related(
                "conversation"
            ).get(user_lower=user_lower, user_higher=user_higher)

            cls.get_logger().debug(
                f"Found existing direct conversation {existing_pair.conversation_id} "
                f"between users {user_lower.id} and {user_higher.id}"
            )
            return ServiceResult.success(existing_pair.conversation)

        except DirectConversationPair.DoesNotExist:
            pass  # Continue to create new conversation

        # Create new conversation within transaction
        with transaction.atomic():
            conversation = Conversation.objects.create(
                conversation_type=ConversationType.DIRECT,
                title="",
                created_by=None,  # Direct conversations are system-created
                participant_count=2,
            )

            # Create DirectConversationPair for uniqueness
            DirectConversationPair.objects.create(
                conversation=conversation,
                user_lower=user_lower,
                user_higher=user_higher,
            )

            # Create participants (no roles for direct conversations)
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

        cls.get_logger().info(
            f"Created direct conversation {conversation.id} "
            f"between users {user_lower.id} and {user_higher.id}"
        )

        return ServiceResult.success(conversation)

    @classmethod
    def create_group(
        cls,
        creator: User,
        title: str,
        initial_members: list[User] | None = None,
    ) -> ServiceResult[Conversation]:
        """
        Create a new group conversation.

        The creator automatically becomes the owner. Initial members
        are added as members (not admins). A system message is created
        to record the group creation event.

        Args:
            creator: User creating the group (becomes owner)
            title: Required group title (cannot be empty)
            initial_members: Optional list of users to add as members

        Returns:
            ServiceResult with new Conversation

        Error codes:
            TITLE_REQUIRED: Group title cannot be empty
        """
        # Validate title
        title = title.strip() if title else ""
        if not title:
            return ServiceResult.failure(
                "Group title is required",
                error_code="TITLE_REQUIRED",
            )

        # Filter out creator from initial_members if present
        members = []
        if initial_members:
            members = [u for u in initial_members if u.id != creator.id]

        with transaction.atomic():
            # Create conversation
            conversation = Conversation.objects.create(
                conversation_type=ConversationType.GROUP,
                title=title,
                created_by=creator,
                participant_count=1 + len(members),
            )

            # Create owner participant
            Participant.objects.create(
                conversation=conversation,
                user=creator,
                role=ParticipantRole.OWNER,
            )

            # Create member participants
            for member in members:
                Participant.objects.create(
                    conversation=conversation,
                    user=member,
                    role=ParticipantRole.MEMBER,
                )

            # Create system message for group creation
            MessageService._create_system_message(
                conversation=conversation,
                event=SystemMessageEvent.GROUP_CREATED,
                data={"title": title},
            )

            # Create system messages for each member added
            for member in members:
                MessageService._create_system_message(
                    conversation=conversation,
                    event=SystemMessageEvent.PARTICIPANT_ADDED,
                    data={
                        "user_id": str(member.id),
                        "added_by_id": str(creator.id),
                    },
                )

        cls.get_logger().info(
            f"Created group conversation {conversation.id} "
            f"titled '{title}' with {1 + len(members)} participants"
        )

        return ServiceResult.success(conversation)

    @classmethod
    def update_title(
        cls,
        conversation: Conversation,
        user: User,
        new_title: str,
    ) -> ServiceResult[Conversation]:
        """
        Update group conversation title.

        Only admins and owners can update the title.
        Direct conversations cannot have titles.

        Args:
            conversation: Group conversation to update
            user: User making the change
            new_title: New title (cannot be empty)

        Returns:
            ServiceResult with updated Conversation

        Error codes:
            NOT_GROUP: Cannot set title on direct conversations
            TITLE_REQUIRED: Title cannot be empty
            NOT_PARTICIPANT: User is not in this conversation
            PERMISSION_DENIED: User lacks permission (not admin/owner)
        """
        # Validate conversation type
        if conversation.is_direct:
            return ServiceResult.failure(
                "Cannot set title on direct conversations",
                error_code="NOT_GROUP",
            )

        # Validate title
        new_title = new_title.strip() if new_title else ""
        if not new_title:
            return ServiceResult.failure(
                "Group title cannot be empty",
                error_code="TITLE_REQUIRED",
            )

        # Check user is participant
        participant = conversation.get_active_participant_for_user(user)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Check permission (admin or owner)
        if not participant.is_admin_or_owner:
            return ServiceResult.failure(
                "Only admins and owners can change the group title",
                error_code="PERMISSION_DENIED",
            )

        old_title = conversation.title

        with transaction.atomic():
            conversation.title = new_title
            conversation.save(update_fields=["title", "updated_at"])

            # Create system message
            MessageService._create_system_message(
                conversation=conversation,
                event=SystemMessageEvent.TITLE_CHANGED,
                data={
                    "old_title": old_title,
                    "new_title": new_title,
                    "changed_by_id": str(user.id),
                },
            )

        cls.get_logger().info(
            f"Updated title of conversation {conversation.id} "
            f"from '{old_title}' to '{new_title}' by user {user.id}"
        )

        return ServiceResult.success(conversation)

    @classmethod
    def delete_conversation(
        cls,
        conversation: Conversation,
        user: User,
    ) -> ServiceResult[None]:
        """
        Soft delete a group conversation.

        Only the owner can delete a group conversation.
        Direct conversations cannot be deleted (users must leave instead).

        Args:
            conversation: Conversation to delete
            user: User requesting deletion

        Returns:
            ServiceResult with None on success

        Error codes:
            NOT_GROUP: Cannot delete direct conversations
            NOT_PARTICIPANT: User is not in this conversation
            PERMISSION_DENIED: Only owner can delete groups
        """
        # Validate conversation type
        if conversation.is_direct:
            return ServiceResult.failure(
                "Cannot delete direct conversations. Both users must leave instead.",
                error_code="NOT_GROUP",
            )

        # Check user is participant
        participant = conversation.get_active_participant_for_user(user)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Check permission (owner only)
        if not participant.is_owner:
            return ServiceResult.failure(
                "Only the conversation owner can delete it",
                error_code="PERMISSION_DENIED",
            )

        # Soft delete
        conversation.is_deleted = True
        conversation.deleted_at = timezone.now()
        conversation.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

        cls.get_logger().info(
            f"Soft deleted conversation {conversation.id} by user {user.id}"
        )

        return ServiceResult.success(None)


class ParticipantService(BaseService):
    """
    Service for participant management operations.

    Methods:
        add_participant: Add user to group conversation
        remove_participant: Remove user from conversation
        leave: User voluntarily leaves conversation
        change_role: Change participant's role
        transfer_ownership: Transfer group ownership
    """

    @classmethod
    def add_participant(
        cls,
        conversation: Conversation,
        user_to_add: User,
        added_by: User,
        role: str = ParticipantRole.MEMBER,
    ) -> ServiceResult[Participant]:
        """
        Add a user to a group conversation.

        Permission rules:
        - Owner: can add members and admins
        - Admin: can only add members
        - Member: cannot add anyone

        Args:
            conversation: Group conversation
            user_to_add: User to add
            added_by: User performing the action
            role: Role to assign (default: MEMBER)

        Returns:
            ServiceResult with new Participant

        Error codes:
            NOT_GROUP: Cannot add participants to direct conversations
            ALREADY_PARTICIPANT: User is already in this conversation
            NOT_PARTICIPANT: Adding user is not in this conversation
            PERMISSION_DENIED: User lacks permission to add
            CANNOT_ADD_OWNER: Cannot add someone as owner
        """
        # Validate conversation type
        if conversation.is_direct:
            return ServiceResult.failure(
                "Cannot add participants to direct conversations",
                error_code="NOT_GROUP",
            )

        # Validate role
        if role == ParticipantRole.OWNER:
            return ServiceResult.failure(
                "Cannot add someone as owner. Use transfer_ownership instead.",
                error_code="CANNOT_ADD_OWNER",
            )

        # Check adding user is participant
        adder_participant = conversation.get_active_participant_for_user(added_by)
        if not adder_participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Check permission based on role
        if adder_participant.is_member:
            return ServiceResult.failure(
                "Members cannot add participants",
                error_code="PERMISSION_DENIED",
            )

        if role == ParticipantRole.ADMIN and not adder_participant.is_owner:
            return ServiceResult.failure(
                "Only owners can add admins",
                error_code="PERMISSION_DENIED",
            )

        # Check if user is already an active participant
        existing = conversation.get_active_participant_for_user(user_to_add)
        if existing:
            return ServiceResult.failure(
                "User is already a participant in this conversation",
                error_code="ALREADY_PARTICIPANT",
            )

        with transaction.atomic():
            # Create new participant record
            participant = Participant.objects.create(
                conversation=conversation,
                user=user_to_add,
                role=role,
            )

            # Update participant count
            conversation.participant_count = F("participant_count") + 1
            conversation.save(update_fields=["participant_count", "updated_at"])
            conversation.refresh_from_db()

            # Create system message
            MessageService._create_system_message(
                conversation=conversation,
                event=SystemMessageEvent.PARTICIPANT_ADDED,
                data={
                    "user_id": str(user_to_add.id),
                    "added_by_id": str(added_by.id),
                },
            )

        cls.get_logger().info(
            f"Added user {user_to_add.id} to conversation {conversation.id} "
            f"as {role} by user {added_by.id}"
        )

        return ServiceResult.success(participant)

    @classmethod
    def remove_participant(
        cls,
        conversation: Conversation,
        user_to_remove: User,
        removed_by: User,
    ) -> ServiceResult[None]:
        """
        Remove a user from a group conversation.

        Permission rules:
        - Owner: can remove admins and members
        - Admin: can only remove members
        - Member: cannot remove anyone (use leave() instead)

        Args:
            conversation: Group conversation
            user_to_remove: User to remove
            removed_by: User performing the action

        Returns:
            ServiceResult with None on success

        Error codes:
            NOT_GROUP: Cannot remove from direct conversations
            NOT_PARTICIPANT: User to remove is not in conversation
            REMOVER_NOT_PARTICIPANT: Removing user is not in conversation
            PERMISSION_DENIED: Insufficient permissions
            CANNOT_REMOVE_OWNER: Cannot remove the owner
            CANNOT_REMOVE_SELF: Use leave() instead
        """
        # Validate conversation type
        if conversation.is_direct:
            return ServiceResult.failure(
                "Cannot remove participants from direct conversations. Use leave instead.",
                error_code="NOT_GROUP",
            )

        # Check for self-removal
        if user_to_remove.id == removed_by.id:
            return ServiceResult.failure(
                "Use leave() to remove yourself from a conversation",
                error_code="CANNOT_REMOVE_SELF",
            )

        # Get participant records
        remover_participant = conversation.get_active_participant_for_user(removed_by)
        if not remover_participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="REMOVER_NOT_PARTICIPANT",
            )

        target_participant = conversation.get_active_participant_for_user(
            user_to_remove
        )
        if not target_participant:
            return ServiceResult.failure(
                "User is not an active participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Cannot remove owner
        if target_participant.is_owner:
            return ServiceResult.failure(
                "Cannot remove the owner. Transfer ownership first.",
                error_code="CANNOT_REMOVE_OWNER",
            )

        # Check permission based on roles
        if remover_participant.is_member:
            return ServiceResult.failure(
                "Members cannot remove other participants",
                error_code="PERMISSION_DENIED",
            )

        if remover_participant.is_admin and target_participant.is_admin:
            return ServiceResult.failure(
                "Admins cannot remove other admins",
                error_code="PERMISSION_DENIED",
            )

        with transaction.atomic():
            # Mark participant as removed
            target_participant.left_at = timezone.now()
            target_participant.left_voluntarily = False
            target_participant.removed_by = removed_by
            target_participant.save(
                update_fields=[
                    "left_at",
                    "left_voluntarily",
                    "removed_by",
                    "updated_at",
                ]
            )

            # Update participant count
            conversation.participant_count = F("participant_count") - 1
            conversation.save(update_fields=["participant_count", "updated_at"])
            conversation.refresh_from_db()

            # Create system message
            MessageService._create_system_message(
                conversation=conversation,
                event=SystemMessageEvent.PARTICIPANT_REMOVED,
                data={
                    "user_id": str(user_to_remove.id),
                    "removed_by_id": str(removed_by.id),
                    "reason": "removed",
                },
            )

        cls.get_logger().info(
            f"Removed user {user_to_remove.id} from conversation {conversation.id} "
            f"by user {removed_by.id}"
        )

        return ServiceResult.success(None)

    @classmethod
    def leave(
        cls,
        conversation: Conversation,
        user: User,
    ) -> ServiceResult[None]:
        """
        User voluntarily leaves a conversation.

        Behavior by conversation type:
        - Direct: User's participation is marked as left. Conversation is
                  soft-deleted only when BOTH users have left.
        - Group: If user is owner, ownership is transferred first (to oldest
                 admin, then oldest member). If no participants remain after
                 leaving, conversation is soft-deleted.

        Args:
            conversation: Conversation to leave
            user: User leaving

        Returns:
            ServiceResult with None on success

        Error codes:
            NOT_PARTICIPANT: User is not in this conversation
        """
        participant = conversation.get_active_participant_for_user(user)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        with transaction.atomic():
            # Handle ownership transfer for groups if user is owner
            if conversation.is_group and participant.is_owner:
                cls._transfer_ownership_on_departure(
                    conversation=conversation,
                    departing_owner=user,
                )

            # Mark participant as left
            participant.left_at = timezone.now()
            participant.left_voluntarily = True
            participant.removed_by = None
            participant.save(
                update_fields=[
                    "left_at",
                    "left_voluntarily",
                    "removed_by",
                    "updated_at",
                ]
            )

            # Update participant count
            conversation.participant_count = F("participant_count") - 1
            conversation.save(update_fields=["participant_count", "updated_at"])
            conversation.refresh_from_db()

            # Create system message
            MessageService._create_system_message(
                conversation=conversation,
                event=SystemMessageEvent.PARTICIPANT_REMOVED,
                data={
                    "user_id": str(user.id),
                    "removed_by_id": None,
                    "reason": "left",
                },
            )

            # Check if conversation should be soft-deleted
            should_delete = False

            if conversation.is_direct:
                # Direct: delete only if both users have left
                active_count = conversation.participants.filter(
                    left_at__isnull=True
                ).count()
                should_delete = active_count == 0

            elif conversation.is_group:
                # Group: delete if no participants remain
                should_delete = conversation.participant_count <= 0

            if should_delete:
                conversation.is_deleted = True
                conversation.deleted_at = timezone.now()
                conversation.save(
                    update_fields=["is_deleted", "deleted_at", "updated_at"]
                )
                cls.get_logger().info(
                    f"Soft deleted conversation {conversation.id} (no remaining participants)"
                )

        cls.get_logger().info(f"User {user.id} left conversation {conversation.id}")

        return ServiceResult.success(None)

    @classmethod
    def change_role(
        cls,
        conversation: Conversation,
        user_to_change: User,
        new_role: str,
        changed_by: User,
    ) -> ServiceResult[Participant]:
        """
        Change a participant's role in a group conversation.

        Permission rules:
        - Only owner can change roles
        - Cannot change owner's role (must transfer ownership instead)
        - Cannot change to OWNER role (use transfer_ownership instead)

        Args:
            conversation: Group conversation
            user_to_change: User whose role to change
            new_role: New role (ADMIN or MEMBER)
            changed_by: User making the change

        Returns:
            ServiceResult with updated Participant

        Error codes:
            NOT_GROUP: Direct conversations don't have roles
            NOT_PARTICIPANT: User to change is not active
            CHANGER_NOT_PARTICIPANT: Changing user is not in conversation
            PERMISSION_DENIED: Only owner can change roles
            CANNOT_CHANGE_OWNER_ROLE: Must transfer ownership instead
            INVALID_ROLE: Cannot change to OWNER role
        """
        # Validate conversation type
        if conversation.is_direct:
            return ServiceResult.failure(
                "Direct conversations do not have roles",
                error_code="NOT_GROUP",
            )

        # Validate new role
        if new_role == ParticipantRole.OWNER:
            return ServiceResult.failure(
                "Cannot change to owner role. Use transfer_ownership instead.",
                error_code="INVALID_ROLE",
            )

        if new_role not in (ParticipantRole.ADMIN, ParticipantRole.MEMBER):
            return ServiceResult.failure(
                f"Invalid role: {new_role}",
                error_code="INVALID_ROLE",
            )

        # Get participant records
        changer_participant = conversation.get_active_participant_for_user(changed_by)
        if not changer_participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="CHANGER_NOT_PARTICIPANT",
            )

        target_participant = conversation.get_active_participant_for_user(
            user_to_change
        )
        if not target_participant:
            return ServiceResult.failure(
                "User is not an active participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Only owner can change roles
        if not changer_participant.is_owner:
            return ServiceResult.failure(
                "Only the owner can change participant roles",
                error_code="PERMISSION_DENIED",
            )

        # Cannot change owner's role
        if target_participant.is_owner:
            return ServiceResult.failure(
                "Cannot change the owner's role. Use transfer_ownership instead.",
                error_code="CANNOT_CHANGE_OWNER_ROLE",
            )

        old_role = target_participant.role

        # Skip if role is unchanged
        if old_role == new_role:
            return ServiceResult.success(target_participant)

        with transaction.atomic():
            target_participant.role = new_role
            target_participant.save(update_fields=["role", "updated_at"])

            # Create system message
            MessageService._create_system_message(
                conversation=conversation,
                event=SystemMessageEvent.ROLE_CHANGED,
                data={
                    "user_id": str(user_to_change.id),
                    "old_role": old_role,
                    "new_role": new_role,
                    "changed_by_id": str(changed_by.id),
                },
            )

        cls.get_logger().info(
            f"Changed role of user {user_to_change.id} in conversation {conversation.id} "
            f"from {old_role} to {new_role} by user {changed_by.id}"
        )

        return ServiceResult.success(target_participant)

    @classmethod
    def transfer_ownership(
        cls,
        conversation: Conversation,
        new_owner: User,
        current_owner: User,
    ) -> ServiceResult[None]:
        """
        Transfer group ownership to another participant.

        The current owner becomes an admin after the transfer.
        The new owner must be an active participant.

        Args:
            conversation: Group conversation
            new_owner: User to receive ownership
            current_owner: Current owner making the transfer

        Returns:
            ServiceResult with None on success

        Error codes:
            NOT_GROUP: Direct conversations don't have ownership
            NOT_OWNER: Only current owner can transfer
            NOT_PARTICIPANT: New owner is not in conversation
            CURRENT_NOT_PARTICIPANT: Current owner is not active
            SAME_USER: Cannot transfer to yourself
        """
        # Validate conversation type
        if conversation.is_direct:
            return ServiceResult.failure(
                "Direct conversations do not have ownership",
                error_code="NOT_GROUP",
            )

        # Cannot transfer to self
        if new_owner.id == current_owner.id:
            return ServiceResult.failure(
                "Cannot transfer ownership to yourself",
                error_code="SAME_USER",
            )

        # Get current owner's participant record
        current_participant = conversation.get_active_participant_for_user(
            current_owner
        )
        if not current_participant:
            return ServiceResult.failure(
                "You are not an active participant in this conversation",
                error_code="CURRENT_NOT_PARTICIPANT",
            )

        if not current_participant.is_owner:
            return ServiceResult.failure(
                "Only the current owner can transfer ownership",
                error_code="NOT_OWNER",
            )

        # Get new owner's participant record
        new_participant = conversation.get_active_participant_for_user(new_owner)
        if not new_participant:
            return ServiceResult.failure(
                "The new owner must be an active participant",
                error_code="NOT_PARTICIPANT",
            )

        with transaction.atomic():
            # Demote current owner to admin
            current_participant.role = ParticipantRole.ADMIN
            current_participant.save(update_fields=["role", "updated_at"])

            # Promote new owner
            new_participant.role = ParticipantRole.OWNER
            new_participant.save(update_fields=["role", "updated_at"])

            # Create system message
            MessageService._create_system_message(
                conversation=conversation,
                event=SystemMessageEvent.OWNERSHIP_TRANSFERRED,
                data={
                    "from_user_id": str(current_owner.id),
                    "to_user_id": str(new_owner.id),
                    "reason": "manual",
                },
            )

        cls.get_logger().info(
            f"Transferred ownership of conversation {conversation.id} "
            f"from user {current_owner.id} to user {new_owner.id}"
        )

        return ServiceResult.success(None)

    @classmethod
    def _transfer_ownership_on_departure(
        cls,
        conversation: Conversation,
        departing_owner: User,
    ) -> User | None:
        """
        Internal: Handle ownership transfer when owner leaves.

        Transfer priority:
        1. Oldest admin (by joined_at)
        2. Oldest member (by joined_at)
        3. None (if no eligible participants)

        This method is called within an existing transaction.

        Args:
            conversation: Group conversation
            departing_owner: Owner who is leaving

        Returns:
            New owner User, or None if no eligible participants

        Side effects:
            - Updates roles in database
            - Creates OWNERSHIP_TRANSFERRED system message
        """
        # Find eligible participants (active, not the departing owner)
        candidates = (
            conversation.participants.filter(left_at__isnull=True)
            .exclude(user=departing_owner)
            .select_related("user")
            .order_by("joined_at")
        )

        # Try to find an admin first
        admin_candidate = candidates.filter(role=ParticipantRole.ADMIN).first()
        if admin_candidate:
            admin_candidate.role = ParticipantRole.OWNER
            admin_candidate.save(update_fields=["role", "updated_at"])

            MessageService._create_system_message(
                conversation=conversation,
                event=SystemMessageEvent.OWNERSHIP_TRANSFERRED,
                data={
                    "from_user_id": str(departing_owner.id),
                    "to_user_id": str(admin_candidate.user.id),
                    "reason": "departure",
                },
            )

            cls.get_logger().info(
                f"Auto-transferred ownership of conversation {conversation.id} "
                f"to admin {admin_candidate.user.id} (owner departed)"
            )
            return admin_candidate.user

        # Try to find a member
        member_candidate = candidates.filter(role=ParticipantRole.MEMBER).first()
        if member_candidate:
            member_candidate.role = ParticipantRole.OWNER
            member_candidate.save(update_fields=["role", "updated_at"])

            MessageService._create_system_message(
                conversation=conversation,
                event=SystemMessageEvent.OWNERSHIP_TRANSFERRED,
                data={
                    "from_user_id": str(departing_owner.id),
                    "to_user_id": str(member_candidate.user.id),
                    "reason": "departure",
                },
            )

            cls.get_logger().info(
                f"Auto-transferred ownership of conversation {conversation.id} "
                f"to member {member_candidate.user.id} (owner departed)"
            )
            return member_candidate.user

        # No eligible candidates - conversation will be deleted
        cls.get_logger().info(
            f"No eligible candidates for ownership transfer in conversation {conversation.id}"
        )
        return None


class MessageService(BaseService):
    """
    Service for message operations.

    Methods:
        send_message: Send a text message
        delete_message: Soft delete a message
        mark_as_read: Update last_read_at for user
        get_unread_count: Get count of unread messages
    """

    @classmethod
    def send_message(
        cls,
        conversation: Conversation,
        sender: User,
        content: str,
        parent_message_id: int | None = None,
    ) -> ServiceResult[Message]:
        """
        Send a text message to a conversation.

        Threading behavior:
        - If parent_message is a reply, the new message references the root
          message instead (single-level threading).
        - This flattening is done automatically.

        Args:
            conversation: Target conversation
            sender: User sending the message
            content: Message text
            parent_message_id: Optional ID of message to reply to

        Returns:
            ServiceResult with new Message

        Error codes:
            NOT_PARTICIPANT: User is not active in conversation
            EMPTY_CONTENT: Message content cannot be empty
            INVALID_PARENT: Parent message not in this conversation
            CONVERSATION_DELETED: Cannot send to deleted conversation
        """
        # Check conversation is not deleted
        if conversation.is_deleted:
            return ServiceResult.failure(
                "Cannot send messages to a deleted conversation",
                error_code="CONVERSATION_DELETED",
            )

        # Check user is active participant
        participant = conversation.get_active_participant_for_user(sender)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Validate content
        content = content.strip() if content else ""
        if not content:
            return ServiceResult.failure(
                "Message content cannot be empty",
                error_code="EMPTY_CONTENT",
            )

        # Handle parent message for threading
        parent_message = None
        if parent_message_id:
            try:
                parent_message = Message.objects.get(
                    id=parent_message_id,
                    conversation=conversation,
                )
                # Flatten threading: if parent is a reply, use its parent instead
                if parent_message.parent_message_id:
                    parent_message = parent_message.parent_message
            except Message.DoesNotExist:
                return ServiceResult.failure(
                    "Parent message not found in this conversation",
                    error_code="INVALID_PARENT",
                )

        with transaction.atomic():
            # Create message
            message = Message.objects.create(
                conversation=conversation,
                sender=sender,
                message_type=MessageType.TEXT,
                content=content,
                parent_message=parent_message,
            )

            # Update conversation last_message_at
            conversation.last_message_at = message.created_at
            conversation.save(update_fields=["last_message_at", "updated_at"])

            # Update parent's reply_count if this is a reply
            if parent_message:
                parent_message.reply_count = F("reply_count") + 1
                parent_message.save(update_fields=["reply_count", "updated_at"])

        cls.get_logger().debug(
            f"User {sender.id} sent message {message.id} "
            f"to conversation {conversation.id}"
        )

        return ServiceResult.success(message)

    @classmethod
    def delete_message(
        cls,
        message: Message,
        user: User,
    ) -> ServiceResult[None]:
        """
        Soft delete a message.

        Users can only delete their own messages.
        Group owners can delete any message in their groups.

        Args:
            message: Message to delete
            user: User requesting deletion

        Returns:
            ServiceResult with None on success

        Error codes:
            NOT_PARTICIPANT: User is not in this conversation
            PERMISSION_DENIED: Can only delete own messages
            ALREADY_DELETED: Message is already deleted
            SYSTEM_MESSAGE: Cannot delete system messages
        """
        conversation = message.conversation

        # Cannot delete system messages
        if message.is_system_message:
            return ServiceResult.failure(
                "Cannot delete system messages",
                error_code="SYSTEM_MESSAGE",
            )

        # Check if already deleted
        if message.is_deleted:
            return ServiceResult.failure(
                "Message is already deleted",
                error_code="ALREADY_DELETED",
            )

        # Check user is participant
        participant = conversation.get_active_participant_for_user(user)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Check permission
        is_own_message = message.sender_id == user.id
        is_group_owner = conversation.is_group and participant.is_owner

        if not (is_own_message or is_group_owner):
            return ServiceResult.failure(
                "You can only delete your own messages",
                error_code="PERMISSION_DENIED",
            )

        # Soft delete
        message.is_deleted = True
        message.deleted_at = timezone.now()
        message.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

        cls.get_logger().info(
            f"User {user.id} deleted message {message.id} "
            f"in conversation {conversation.id}"
        )

        return ServiceResult.success(None)

    @classmethod
    def edit_message(
        cls,
        user: User,
        message_id: int,
        new_content: str,
    ) -> ServiceResult[Message]:
        """
        Edit a message within the allowed time window.

        Only the original author can edit their message. Edits are tracked
        with timestamps and counts. The original content is preserved on
        first edit, and each edit creates a history entry.

        Args:
            user: User attempting to edit
            message_id: ID of message to edit
            new_content: New message content

        Returns:
            ServiceResult with updated Message

        Error codes:
            MESSAGE_NOT_FOUND: Message does not exist
            NOT_AUTHOR: User is not the message author
            EDIT_TIME_EXPIRED: Edit time window has passed
            MAX_EDITS_REACHED: Maximum edit count reached
            MESSAGE_DELETED: Cannot edit deleted messages
            SYSTEM_MESSAGE: Cannot edit system messages
            EMPTY_CONTENT: Content cannot be empty
        """
        # Validate content
        new_content = new_content.strip() if new_content else ""
        if not new_content:
            return ServiceResult.failure(
                "Message content cannot be empty",
                error_code="EMPTY_CONTENT",
            )

        # Get the message
        try:
            message = Message.objects.select_related("conversation").get(id=message_id)
        except Message.DoesNotExist:
            return ServiceResult.failure(
                "Message not found",
                error_code="MESSAGE_NOT_FOUND",
            )

        # Cannot edit system messages
        if message.is_system_message:
            return ServiceResult.failure(
                "Cannot edit system messages",
                error_code="SYSTEM_MESSAGE",
            )

        # Cannot edit deleted messages
        if message.is_deleted:
            return ServiceResult.failure(
                "Cannot edit deleted messages",
                error_code="MESSAGE_DELETED",
            )

        # Check user is the author
        if message.sender_id != user.id:
            return ServiceResult.failure(
                "You can only edit your own messages",
                error_code="NOT_AUTHOR",
            )

        # Check edit time limit
        time_since_creation = timezone.now() - message.created_at
        if time_since_creation.total_seconds() > MESSAGE_CONFIG.EDIT_TIME_LIMIT_SECONDS:
            return ServiceResult.failure(
                "Edit time window has expired",
                error_code="EDIT_TIME_EXPIRED",
            )

        with transaction.atomic():
            # Re-fetch with lock to prevent race conditions on edit_count
            message = Message.objects.select_for_update().get(id=message_id)

            # Re-check conditions that could have changed
            if message.is_deleted:
                return ServiceResult.failure(
                    "Cannot edit deleted messages",
                    error_code="MESSAGE_DELETED",
                )

            # Check max edit count (must be inside lock to prevent race)
            if message.edit_count >= MESSAGE_CONFIG.MAX_EDIT_COUNT:
                return ServiceResult.failure(
                    f"Maximum edit count ({MESSAGE_CONFIG.MAX_EDIT_COUNT}) reached",
                    error_code="MAX_EDITS_REACHED",
                )

            # Preserve original content on first edit
            if message.edit_count == 0 and MESSAGE_CONFIG.PRESERVE_ORIGINAL_CONTENT:
                message.original_content = message.content

            # Create edit history entry
            MessageEditHistory.objects.create(
                message=message,
                content=message.content,  # Store the content before this edit
                edit_number=message.edit_count + 1,
            )

            # Update message
            message.content = new_content
            message.edit_count = F("edit_count") + 1
            message.edited_at = timezone.now()
            message.save(
                update_fields=[
                    "content",
                    "original_content",
                    "edit_count",
                    "edited_at",
                    "updated_at",
                ]
            )

            # Refresh to get the actual edit_count value (not F() expression)
            message.refresh_from_db()

        cls.get_logger().info(
            f"User {user.id} edited message {message.id} (edit #{message.edit_count})"
        )

        return ServiceResult.success(message)

    @classmethod
    def get_edit_history(
        cls,
        user: User,
        message_id: int,
    ) -> ServiceResult[list[MessageEditHistory]]:
        """
        Get edit history for a message.

        Only conversation participants can view edit history.

        Args:
            user: User requesting history
            message_id: ID of message to get history for

        Returns:
            ServiceResult with list of MessageEditHistory entries (newest first)

        Error codes:
            MESSAGE_NOT_FOUND: Message does not exist
            NOT_PARTICIPANT: User is not in the conversation
        """
        # Get the message
        try:
            message = Message.objects.select_related("conversation").get(id=message_id)
        except Message.DoesNotExist:
            return ServiceResult.failure(
                "Message not found",
                error_code="MESSAGE_NOT_FOUND",
            )

        # Check user is a participant in the conversation
        participant = message.conversation.get_active_participant_for_user(user)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Get edit history (ordered by created_at descending per model Meta)
        history = list(message.edit_history.all())

        return ServiceResult.success(history)

    @classmethod
    def mark_as_read(
        cls,
        conversation: Conversation,
        user: User,
    ) -> ServiceResult[None]:
        """
        Mark conversation as read for a user.

        Updates the participant's last_read_at timestamp to the current time.

        Args:
            conversation: Conversation to mark as read
            user: User marking as read

        Returns:
            ServiceResult with None on success

        Error codes:
            NOT_PARTICIPANT: User is not in this conversation
        """
        participant = conversation.get_active_participant_for_user(user)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        participant.last_read_at = timezone.now()
        participant.save(update_fields=["last_read_at", "updated_at"])

        cls.get_logger().debug(
            f"User {user.id} marked conversation {conversation.id} as read"
        )

        return ServiceResult.success(None)

    @classmethod
    def get_unread_count(
        cls,
        conversation: Conversation,
        user: User,
    ) -> int:
        """
        Get count of unread messages for a user in a conversation.

        Unread messages are those created after the user's last_read_at.
        If last_read_at is None, all messages are considered unread.
        Messages from the user themselves are excluded from the count.

        Args:
            conversation: Conversation to count unread messages
            user: User to get unread count for

        Returns:
            Number of unread messages (0 if user is not a participant)
        """
        participant = conversation.get_active_participant_for_user(user)
        if not participant:
            return 0

        queryset = conversation.messages.exclude(sender=user)

        if participant.last_read_at:
            queryset = queryset.filter(created_at__gt=participant.last_read_at)

        return queryset.count()

    @classmethod
    def _create_system_message(
        cls,
        conversation: Conversation,
        event: str,
        data: dict,
    ) -> Message:
        """
        Internal: Create a system event message.

        System messages have:
        - sender = None (system-generated)
        - message_type = SYSTEM
        - content = JSON with {event, data}

        This method should be called within an existing transaction.

        Args:
            conversation: Target conversation
            event: Event type from SystemMessageEvent
            data: Event-specific payload

        Returns:
            Created Message
        """
        content = json.dumps({"event": event, "data": data})

        message = Message.objects.create(
            conversation=conversation,
            sender=None,
            message_type=MessageType.SYSTEM,
            content=content,
        )

        # Update conversation last_message_at
        conversation.last_message_at = message.created_at
        conversation.save(update_fields=["last_message_at", "updated_at"])

        return message


# =============================================================================
# ReactionService
# =============================================================================


class ReactionService:
    """
    Service for managing message reactions.

    Handles:
    - Adding reactions to messages
    - Removing reactions from messages
    - Toggle reaction (add/remove)
    - Getting reactions for messages
    - Atomic count updates in Message.reaction_counts
    """

    @classmethod
    def _validate_emoji(cls, emoji: str) -> bool:
        """
        Validate that emoji is a valid reaction emoji.

        Args:
            emoji: The emoji string to validate

        Returns:
            True if valid, False otherwise
        """
        if not emoji or not emoji.strip():
            return False

        emoji = emoji.strip()

        # Check max length to prevent abuse
        if len(emoji) > REACTION_CONFIG.MAX_EMOJI_LENGTH:
            return False

        # If ALLOWED_EMOJIS is set, check against it
        if REACTION_CONFIG.ALLOWED_EMOJIS is not None:
            return emoji in REACTION_CONFIG.ALLOWED_EMOJIS

        # Basic validation: emoji should be at least 1 character
        return len(emoji) >= 1

    @classmethod
    def _update_reaction_count(
        cls,
        message: Message,
        emoji: str,
        delta: int,
    ) -> None:
        """
        Atomically update reaction count for an emoji.

        Uses select_for_update() to lock the row and prevent race conditions.
        Must be called within a transaction.atomic() block.

        Args:
            message: Message to update
            emoji: Emoji to update count for
            delta: Amount to change (1 to add, -1 to remove)
        """
        # Lock the row to prevent concurrent modifications
        locked_message = Message.objects.select_for_update().get(id=message.id)
        counts = locked_message.reaction_counts or {}

        current_count = counts.get(emoji, 0)
        new_count = max(0, current_count + delta)

        if new_count == 0:
            # Remove the emoji key when count reaches 0
            counts.pop(emoji, None)
        else:
            counts[emoji] = new_count

        locked_message.reaction_counts = counts
        locked_message.save(update_fields=["reaction_counts", "updated_at"])

    @classmethod
    def add_reaction(
        cls,
        user: "User",
        message_id: int,
        emoji: str,
    ) -> ServiceResult[MessageReaction]:
        """
        Add a reaction to a message.

        If the reaction already exists, returns the existing one (idempotent).
        Updates Message.reaction_counts atomically.

        Args:
            user: User adding the reaction
            message_id: ID of message to react to
            emoji: Emoji to add

        Returns:
            ServiceResult containing the MessageReaction on success
        """
        # Validate emoji
        if not cls._validate_emoji(emoji):
            return ServiceResult.failure(
                "Invalid emoji",
                error_code="INVALID_EMOJI",
            )

        # Get message
        try:
            message = Message.objects.select_related("conversation").get(id=message_id)
        except Message.DoesNotExist:
            return ServiceResult.failure(
                "Message not found",
                error_code="MESSAGE_NOT_FOUND",
            )

        # Check if message is deleted
        if message.is_deleted:
            return ServiceResult.failure(
                "Cannot react to deleted messages",
                error_code="MESSAGE_DELETED",
            )

        # Check user is participant
        participant = message.conversation.get_active_participant_for_user(user)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Check max reactions per user per message limit
        user_reaction_count = MessageReaction.objects.filter(
            message=message,
            user=user,
        ).count()

        if user_reaction_count >= REACTION_CONFIG.MAX_USER_REACTIONS_PER_MESSAGE:
            return ServiceResult.failure(
                f"Maximum reactions per message ({REACTION_CONFIG.MAX_USER_REACTIONS_PER_MESSAGE}) exceeded",
                error_code="MAX_REACTIONS_EXCEEDED",
            )

        # Check if reaction already exists (idempotent)
        existing = MessageReaction.objects.filter(
            message=message,
            user=user,
            emoji=emoji,
        ).first()

        if existing:
            return ServiceResult.success(existing)

        # Create reaction
        with transaction.atomic():
            reaction = MessageReaction.objects.create(
                message=message,
                user=user,
                emoji=emoji,
            )

            # Update count
            cls._update_reaction_count(message, emoji, 1)

        return ServiceResult.success(reaction)

    @classmethod
    def remove_reaction(
        cls,
        user: "User",
        message_id: int,
        emoji: str,
    ) -> ServiceResult[None]:
        """
        Remove a reaction from a message.

        Updates Message.reaction_counts atomically.

        Args:
            user: User removing the reaction
            message_id: ID of message
            emoji: Emoji to remove

        Returns:
            ServiceResult (data is None on success)
        """
        # Get message
        try:
            message = Message.objects.select_related("conversation").get(id=message_id)
        except Message.DoesNotExist:
            return ServiceResult.failure(
                "Message not found",
                error_code="MESSAGE_NOT_FOUND",
            )

        # Check user is participant
        participant = message.conversation.get_active_participant_for_user(user)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Find reaction
        reaction = MessageReaction.objects.filter(
            message=message,
            user=user,
            emoji=emoji,
        ).first()

        if not reaction:
            return ServiceResult.failure(
                "Reaction not found",
                error_code="REACTION_NOT_FOUND",
            )

        # Delete reaction and update count
        with transaction.atomic():
            reaction.delete()
            cls._update_reaction_count(message, emoji, -1)

        return ServiceResult.success(None)

    @classmethod
    def toggle_reaction(
        cls,
        user: "User",
        message_id: int,
        emoji: str,
    ) -> ServiceResult[tuple[bool, MessageReaction | None]]:
        """
        Toggle a reaction on a message.

        If reaction exists, removes it. If not, adds it.

        Args:
            user: User toggling the reaction
            message_id: ID of message
            emoji: Emoji to toggle

        Returns:
            ServiceResult containing tuple (added: bool, reaction: MessageReaction | None)
            - added=True, reaction=MessageReaction if reaction was added
            - added=False, reaction=None if reaction was removed
        """
        # Validate emoji
        if not cls._validate_emoji(emoji):
            return ServiceResult.failure(
                "Invalid emoji",
                error_code="INVALID_EMOJI",
            )

        # Get message
        try:
            message = Message.objects.select_related("conversation").get(id=message_id)
        except Message.DoesNotExist:
            return ServiceResult.failure(
                "Message not found",
                error_code="MESSAGE_NOT_FOUND",
            )

        # Check if message is deleted
        if message.is_deleted:
            return ServiceResult.failure(
                "Cannot react to deleted messages",
                error_code="MESSAGE_DELETED",
            )

        # Check user is participant
        participant = message.conversation.get_active_participant_for_user(user)
        if not participant:
            return ServiceResult.failure(
                "You are not a participant in this conversation",
                error_code="NOT_PARTICIPANT",
            )

        # Check if reaction exists
        existing = MessageReaction.objects.filter(
            message=message,
            user=user,
            emoji=emoji,
        ).first()

        if existing:
            # Remove reaction
            with transaction.atomic():
                existing.delete()
                cls._update_reaction_count(message, emoji, -1)
            return ServiceResult.success((False, None))
        else:
            # Check max reactions limit before adding
            user_reaction_count = MessageReaction.objects.filter(
                message=message,
                user=user,
            ).count()

            if user_reaction_count >= REACTION_CONFIG.MAX_USER_REACTIONS_PER_MESSAGE:
                return ServiceResult.failure(
                    f"Maximum reactions per message ({REACTION_CONFIG.MAX_USER_REACTIONS_PER_MESSAGE}) exceeded",
                    error_code="MAX_REACTIONS_EXCEEDED",
                )

            # Add reaction
            with transaction.atomic():
                reaction = MessageReaction.objects.create(
                    message=message,
                    user=user,
                    emoji=emoji,
                )
                cls._update_reaction_count(message, emoji, 1)

            return ServiceResult.success((True, reaction))

    @classmethod
    def get_message_reactions(
        cls,
        message_id: int,
    ) -> ServiceResult[dict]:
        """
        Get all reactions for a message grouped by emoji.

        Args:
            message_id: ID of message

        Returns:
            ServiceResult containing dict of emoji -> {count, users: [{id, name}]}
        """
        # Verify message exists
        try:
            message = Message.objects.get(id=message_id)
        except Message.DoesNotExist:
            return ServiceResult.failure(
                "Message not found",
                error_code="MESSAGE_NOT_FOUND",
            )

        # Get all reactions for this message
        reactions = (
            MessageReaction.objects.filter(message=message)
            .select_related("user")
            .order_by("emoji", "created_at")
        )

        # Group by emoji
        result = {}
        for reaction in reactions:
            emoji = reaction.emoji
            if emoji not in result:
                result[emoji] = {"count": 0, "users": []}

            result[emoji]["count"] += 1
            result[emoji]["users"].append(
                {
                    "id": reaction.user.id,
                    "name": reaction.user.get_full_name() or reaction.user.email,
                }
            )

        return ServiceResult.success(result)

    @classmethod
    def get_user_reactions(
        cls,
        user: "User",
        message_ids: list[int],
    ) -> ServiceResult[dict[int, list[str]]]:
        """
        Get user's reactions for multiple messages.

        Useful for UI to highlight which emojis user has used.

        Args:
            user: User to get reactions for
            message_ids: List of message IDs

        Returns:
            ServiceResult containing dict of message_id -> [emoji, ...]
        """
        if not message_ids:
            return ServiceResult.success({})

        # Get all user's reactions for these messages
        reactions = MessageReaction.objects.filter(
            user=user,
            message_id__in=message_ids,
        ).values_list("message_id", "emoji")

        # Group by message
        result = {}
        for message_id, emoji in reactions:
            if message_id not in result:
                result[message_id] = []
            result[message_id].append(emoji)

        return ServiceResult.success(result)


# =============================================================================
# MessageSearchService
# =============================================================================


@dataclass
class SearchCursor:
    """
    Cursor for keyset pagination in search results.

    Uses message_id for simple, reliable pagination since IDs are unique
    and the results are always ordered deterministically.
    """

    last_message_id: int

    def encode(self) -> str:
        """Encode cursor as base64 JSON string."""
        import base64
        import json

        data = {"last_id": self.last_message_id}
        json_str = json.dumps(data)
        return base64.b64encode(json_str.encode()).decode()

    @classmethod
    def decode(cls, encoded: str) -> "SearchCursor":
        """Decode cursor from base64 JSON string."""
        import base64
        import json

        try:
            json_str = base64.b64decode(encoded.encode()).decode()
            data = json.loads(json_str)
            return cls(last_message_id=int(data["last_id"]))
        except Exception:
            raise ValueError("Invalid cursor")


class MessageSearchService:
    """
    Service for full-text search across messages.

    Uses PostgreSQL's full-text search with:
    - Automatic search_vector updates via database trigger
    - English text configuration for stemming
    - Keyset pagination for efficient large result sets

    Handles:
    - Searching user's accessible messages
    - Conversation-scoped search
    - Relevance ranking
    - Cursor-based pagination
    """

    @classmethod
    def _get_user_conversation_ids(cls, user: "User") -> list[int]:
        """
        Get IDs of all conversations the user has access to.

        Args:
            user: User to get conversations for

        Returns:
            List of conversation IDs
        """
        from chat.models import Participant

        return list(
            Participant.objects.filter(
                user=user,
                left_at__isnull=True,
            ).values_list("conversation_id", flat=True)
        )

    @classmethod
    def search(
        cls,
        user: "User",
        query: str,
        conversation_id: int | None = None,
        cursor: str | None = None,
        page_size: int = MESSAGE_CONFIG.SEARCH_DEFAULT_PAGE_SIZE,
    ) -> ServiceResult[dict]:
        """
        Full-text search for messages.

        Args:
            user: User performing the search
            query: Search query string
            conversation_id: Optional - limit search to specific conversation
            cursor: Optional - pagination cursor from previous search
            page_size: Number of results per page

        Returns:
            ServiceResult containing:
            {
                "results": [Message, ...],
                "next_cursor": str | None,
                "has_more": bool
            }
        """
        from django.contrib.postgres.search import SearchQuery, SearchRank
        from django.db.models import F

        # Validate query
        if not query or not query.strip():
            return ServiceResult.failure(
                "Search query is required",
                error_code="INVALID_QUERY",
            )

        query = query.strip()
        if len(query) < MESSAGE_CONFIG.SEARCH_MIN_QUERY_LENGTH:
            return ServiceResult.failure(
                f"Search query must be at least {MESSAGE_CONFIG.SEARCH_MIN_QUERY_LENGTH} characters",
                error_code="QUERY_TOO_SHORT",
            )

        # Validate page size
        page_size = min(page_size, MESSAGE_CONFIG.SEARCH_MAX_RESULTS)

        # Handle conversation filter FIRST (before getting accessible IDs)
        if conversation_id is not None:
            # Check conversation exists
            try:
                Conversation.objects.get(id=conversation_id)
            except Conversation.DoesNotExist:
                return ServiceResult.failure(
                    "Conversation not found",
                    error_code="CONVERSATION_NOT_FOUND",
                )

            # Check user has access to this specific conversation
            is_participant = Participant.objects.filter(
                conversation_id=conversation_id,
                user=user,
                left_at__isnull=True,
            ).exists()

            if not is_participant:
                return ServiceResult.failure(
                    "You don't have access to this conversation",
                    error_code="CONVERSATION_NOT_ACCESSIBLE",
                )

            # Limit search to this conversation
            accessible_ids = [conversation_id]
        else:
            # Get all accessible conversation IDs
            accessible_ids = cls._get_user_conversation_ids(user)
            if not accessible_ids:
                return ServiceResult.success(
                    {
                        "results": [],
                        "next_cursor": None,
                        "has_more": False,
                    }
                )

        # Build search query
        search_query = SearchQuery(query, config="english")

        # Base queryset - only text messages with search_vector
        qs = (
            Message.objects.filter(
                conversation_id__in=accessible_ids,
                is_deleted=False,
                message_type=MessageType.TEXT,
                search_vector__isnull=False,
            )
            .annotate(rank=SearchRank(F("search_vector"), search_query))
            .filter(search_vector=search_query)
        )

        # Handle cursor for pagination
        if cursor:
            try:
                c = SearchCursor.decode(cursor)
                # Simple pagination: exclude messages we've already seen
                # Since we order by -rank, -created_at, -id, we need to exclude
                # all messages with ID >= cursor ID in the same result set
                qs = qs.exclude(id__gte=c.last_message_id)
            except ValueError:
                return ServiceResult.failure(
                    "Invalid pagination cursor",
                    error_code="INVALID_CURSOR",
                )

        # Order by relevance (rank), then by recency, then by id for stability
        qs = qs.order_by("-rank", "-created_at", "-id")

        # Fetch one extra to check if there are more results
        results = list(qs[: page_size + 1])
        has_more = len(results) > page_size
        results = results[:page_size]

        # Build next cursor
        next_cursor = None
        if has_more and results:
            last = results[-1]
            next_cursor = SearchCursor(last_message_id=last.id).encode()

        return ServiceResult.success(
            {
                "results": results,
                "next_cursor": next_cursor,
                "has_more": has_more,
            }
        )


# =============================================================================
# Presence Service
# =============================================================================


class PresenceStatus:
    """User presence status values."""

    ONLINE = "online"
    AWAY = "away"
    OFFLINE = "offline"

    @classmethod
    def is_valid(cls, status: str) -> bool:
        """Check if status value is valid."""
        return status in (cls.ONLINE, cls.AWAY, cls.OFFLINE)


class PresenceService(BaseService):
    """
    Redis-based presence tracking service.

    This service provides real-time presence tracking using Redis:
    - User presence status (online, away, offline)
    - Per-conversation presence (who's viewing which chat)
    - Automatic expiration via TTL
    - Atomic operations using Lua scripts

    Design Decisions:
        - Redis-only storage (no database persistence)
        - TTL-based auto-expiry for stale presence
        - Separate keys for global and per-conversation presence
        - Lua scripts for atomic set/get operations

    Usage:
        from chat.services import PresenceService, PresenceStatus

        # Set user online
        PresenceService.set_presence(user.id, PresenceStatus.ONLINE)

        # Set user online in a conversation
        PresenceService.set_presence(user.id, PresenceStatus.ONLINE, conversation_id=conv.id)

        # Get user presence
        result = PresenceService.get_presence(user.id)

        # Get all participants' presence in a conversation
        result = PresenceService.get_conversation_presence(conv.id)
    """

    # Lua script for atomic presence set with TTL
    # Keys: [user_presence_key]
    # Args: [status, last_seen_timestamp, ttl_seconds]
    LUA_SET_PRESENCE = """
    redis.call('HSET', KEYS[1], 'status', ARGV[1], 'last_seen', ARGV[2])
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    return 1
    """

    # Lua script for atomic conversation presence update
    # Keys: [conversation_presence_key]
    # Args: [user_id, ttl_seconds]
    LUA_SET_CONVERSATION_PRESENCE = """
    redis.call('SADD', KEYS[1], ARGV[1])
    redis.call('EXPIRE', KEYS[1], ARGV[2])
    return 1
    """

    # Cached registered Lua scripts (initialized lazily)
    _lua_set_presence = None
    _lua_set_conversation_presence = None

    @staticmethod
    def _get_cache():
        """Get Django cache backend (Redis)."""
        from django.core.cache import cache

        return cache

    @staticmethod
    def _get_redis_client():
        """
        Get raw Redis client for Lua script execution.

        Returns Redis client from django-redis.
        """
        from django_redis import get_redis_connection

        return get_redis_connection("default")

    @classmethod
    def _get_lua_set_presence(cls, redis_client):
        """Get cached Lua script for setting presence."""
        if cls._lua_set_presence is None:
            cls._lua_set_presence = redis_client.register_script(cls.LUA_SET_PRESENCE)
        return cls._lua_set_presence

    @classmethod
    def _get_lua_set_conversation_presence(cls, redis_client):
        """Get cached Lua script for setting conversation presence."""
        if cls._lua_set_conversation_presence is None:
            cls._lua_set_conversation_presence = redis_client.register_script(
                cls.LUA_SET_CONVERSATION_PRESENCE
            )
        return cls._lua_set_conversation_presence

    @staticmethod
    def _user_presence_key(user_id) -> str:
        """Build Redis key for user presence."""
        return f"{PRESENCE_CONFIG.KEY_PREFIX_USER_PRESENCE}:{user_id}"

    @staticmethod
    def _conversation_presence_key(conversation_id) -> str:
        """Build Redis key for conversation presence."""
        return f"{PRESENCE_CONFIG.KEY_PREFIX_CONVERSATION_PRESENCE}:{conversation_id}"

    @classmethod
    def set_presence(
        cls,
        user_id,
        status: str,
        conversation_id: int | None = None,
    ) -> ServiceResult:
        """
        Set user presence status.

        Args:
            user_id: User's UUID
            status: Presence status (online, away, offline)
            conversation_id: Optional conversation ID for per-conversation presence

        Returns:
            ServiceResult with status data
        """
        if not PresenceStatus.is_valid(status):
            return ServiceResult.failure(
                error=f"Invalid status: {status}",
                error_code="invalid_status",
            )

        # Verify conversation participation if conversation_id provided
        if conversation_id is not None:
            is_participant = Participant.objects.filter(
                conversation_id=conversation_id,
                user_id=user_id,
                left_at__isnull=True,
            ).exists()
            if not is_participant:
                return ServiceResult.failure(
                    error="You are not a participant in this conversation",
                    error_code="not_participant",
                )

        try:
            redis_client = cls._get_redis_client()
            now = timezone.now().isoformat()

            if status == PresenceStatus.OFFLINE:
                # Remove presence entry using raw Redis client
                user_key = cls._user_presence_key(user_id)
                redis_client.delete(user_key)

                # Remove from conversation presence if specified
                if conversation_id:
                    conv_key = cls._conversation_presence_key(conversation_id)
                    redis_client.srem(conv_key, str(user_id))

                return ServiceResult.success(
                    {
                        "user_id": str(user_id),
                        "status": status,
                        "last_seen": now,
                    }
                )

            # Set user presence using Lua script for atomicity
            user_key = cls._user_presence_key(user_id)
            lua_set = cls._get_lua_set_presence(redis_client)
            lua_set(
                keys=[user_key],
                args=[status, now, PRESENCE_CONFIG.PRESENCE_TTL_SECONDS],
            )

            result_data = {
                "user_id": str(user_id),
                "status": status,
                "last_seen": now,
            }

            # Set conversation presence if specified
            if conversation_id:
                conv_key = cls._conversation_presence_key(conversation_id)
                lua_conv = cls._get_lua_set_conversation_presence(redis_client)
                lua_conv(
                    keys=[conv_key],
                    args=[
                        str(user_id),
                        PRESENCE_CONFIG.CONVERSATION_PRESENCE_TTL_SECONDS,
                    ],
                )
                result_data["conversation_id"] = conversation_id

            return ServiceResult.success(result_data)

        except Exception as e:
            logger.exception(f"Error setting presence for user {user_id}: {e}")
            return ServiceResult.failure(
                error="Failed to set presence",
                error_code="presence_error",
            )

    @classmethod
    def get_presence(cls, user_id) -> ServiceResult:
        """
        Get user presence status.

        Args:
            user_id: User's UUID

        Returns:
            ServiceResult with presence data including status and last_seen
        """
        try:
            redis_client = cls._get_redis_client()
            user_key = cls._user_presence_key(user_id)

            presence_data = redis_client.hgetall(user_key)

            if not presence_data:
                # User has no presence entry - considered offline
                return ServiceResult.success(
                    {
                        "user_id": str(user_id),
                        "status": PresenceStatus.OFFLINE,
                        "last_seen": None,
                    }
                )

            # Decode bytes from Redis
            status = presence_data.get(b"status", b"offline").decode("utf-8")
            last_seen = presence_data.get(b"last_seen", b"").decode("utf-8") or None

            return ServiceResult.success(
                {
                    "user_id": str(user_id),
                    "status": status,
                    "last_seen": last_seen,
                }
            )

        except Exception as e:
            logger.exception(f"Error getting presence for user {user_id}: {e}")
            return ServiceResult.failure(
                error="Failed to get presence",
                error_code="presence_error",
            )

    @classmethod
    def get_bulk_presence(cls, user_ids: list) -> ServiceResult:
        """
        Get presence for multiple users.

        Args:
            user_ids: List of user UUIDs

        Returns:
            ServiceResult with list of presence data
        """
        if not user_ids:
            return ServiceResult.success([])

        try:
            redis_client = cls._get_redis_client()
            pipeline = redis_client.pipeline()

            # Queue all HGETALL commands
            for user_id in user_ids:
                user_key = cls._user_presence_key(user_id)
                pipeline.hgetall(user_key)

            # Execute pipeline
            results = pipeline.execute()

            # Build response
            presence_list = []
            for i, user_id in enumerate(user_ids):
                presence_data = results[i]
                if presence_data:
                    status = presence_data.get(b"status", b"offline").decode("utf-8")
                    last_seen = (
                        presence_data.get(b"last_seen", b"").decode("utf-8") or None
                    )
                else:
                    status = PresenceStatus.OFFLINE
                    last_seen = None

                presence_list.append(
                    {
                        "user_id": str(user_id),
                        "status": status,
                        "last_seen": last_seen,
                    }
                )

            return ServiceResult.success(presence_list)

        except Exception as e:
            logger.exception(f"Error getting bulk presence: {e}")
            return ServiceResult.failure(
                error="Failed to get bulk presence",
                error_code="presence_error",
            )

    @classmethod
    def get_conversation_presence(cls, conversation_id: int) -> ServiceResult:
        """
        Get presence of all users in a conversation.

        Only returns users who are currently online or away in the conversation.

        Args:
            conversation_id: Conversation ID

        Returns:
            ServiceResult with list of presence data for active users
        """
        try:
            redis_client = cls._get_redis_client()
            conv_key = cls._conversation_presence_key(conversation_id)

            # Get all user IDs present in conversation
            user_ids_bytes = redis_client.smembers(conv_key)

            if not user_ids_bytes:
                return ServiceResult.success([])

            # Convert bytes to strings
            user_ids = [uid.decode("utf-8") for uid in user_ids_bytes]

            # Get presence for all these users
            pipeline = redis_client.pipeline()
            for user_id in user_ids:
                user_key = cls._user_presence_key(user_id)
                pipeline.hgetall(user_key)

            results = pipeline.execute()

            # Filter to only online/away users and build response
            presence_list = []
            for i, user_id in enumerate(user_ids):
                presence_data = results[i]
                if presence_data:
                    status = presence_data.get(b"status", b"offline").decode("utf-8")
                    if status != PresenceStatus.OFFLINE:
                        last_seen = (
                            presence_data.get(b"last_seen", b"").decode("utf-8") or None
                        )
                        presence_list.append(
                            {
                                "user_id": user_id,
                                "status": status,
                                "last_seen": last_seen,
                            }
                        )

            return ServiceResult.success(presence_list)

        except Exception as e:
            logger.exception(f"Error getting conversation presence: {e}")
            return ServiceResult.failure(
                error="Failed to get conversation presence",
                error_code="presence_error",
            )

    @classmethod
    def heartbeat(
        cls,
        user_id,
        conversation_id: int | None = None,
    ) -> ServiceResult:
        """
        Update presence heartbeat.

        Refreshes TTL and updates last_seen timestamp. If user has no presence
        entry, sets them as online.

        Args:
            user_id: User's UUID
            conversation_id: Optional conversation ID to refresh

        Returns:
            ServiceResult with updated presence data
        """
        try:
            # Get current status
            current = cls.get_presence(user_id)
            if not current.success:
                return current

            # Use current status or default to online
            status = current.data["status"]
            if status == PresenceStatus.OFFLINE:
                status = PresenceStatus.ONLINE

            # Set presence (which updates TTL)
            return cls.set_presence(user_id, status, conversation_id)

        except Exception as e:
            logger.exception(f"Error heartbeat for user {user_id}: {e}")
            return ServiceResult.failure(
                error="Failed to process heartbeat",
                error_code="presence_error",
            )

    @classmethod
    def leave_conversation(cls, user_id, conversation_id: int) -> ServiceResult:
        """
        Remove user from conversation presence.

        Does not affect global presence status.

        Args:
            user_id: User's UUID
            conversation_id: Conversation ID

        Returns:
            ServiceResult
        """
        try:
            redis_client = cls._get_redis_client()
            conv_key = cls._conversation_presence_key(conversation_id)

            redis_client.srem(conv_key, str(user_id))

            return ServiceResult.success({"conversation_id": conversation_id})

        except Exception as e:
            logger.exception(f"Error leaving conversation presence: {e}")
            return ServiceResult.failure(
                error="Failed to leave conversation",
                error_code="presence_error",
            )

    @classmethod
    def clear_presence(cls, user_id) -> ServiceResult:
        """
        Clear all presence data for a user.

        Removes global presence and from all conversation presence sets.

        Args:
            user_id: User's UUID

        Returns:
            ServiceResult
        """
        try:
            redis_client = cls._get_redis_client()

            # Delete user presence using raw Redis client
            user_key = cls._user_presence_key(user_id)
            redis_client.delete(user_key)

            # Note: Removing from all conversation sets would require tracking
            # which conversations the user is in. For simplicity, we rely on
            # TTL expiration for conversation presence cleanup.

            return ServiceResult.success({"user_id": str(user_id), "cleared": True})

        except Exception as e:
            logger.exception(f"Error clearing presence for user {user_id}: {e}")
            return ServiceResult.failure(
                error="Failed to clear presence",
                error_code="presence_error",
            )
