"""
Permission classes for chat API.

This module provides DRF permission classes for the chat system:
- IsConversationParticipant: User is an active participant
- IsConversationOwner: User has OWNER role (group only)
- IsConversationAdminOrOwner: User has ADMIN or OWNER role
- CanManageParticipants: Can add/remove participants based on role hierarchy
- CanModifyMessage: Can delete own messages only

Permission Hierarchy:
    OWNER > ADMIN > MEMBER

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

Design Decisions:
    - Permissions check against Participant model, not User
    - Active participant = left_at IS NULL
    - Permission classes are composable via DRF's AND logic
    - View-level permissions use get_object() for efficiency
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import permissions

from chat.models import Conversation, ConversationType, Participant, ParticipantRole

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView


class IsConversationParticipant(permissions.BasePermission):
    """
    Allows access only to active participants of the conversation.

    This is the base permission for most chat endpoints.
    Checks that the user has an active (not left) participation record.
    """

    message = "You are not a participant in this conversation."

    def has_object_permission(
        self, request: Request, view: APIView, obj: Conversation | Participant
    ) -> bool:
        """Check if user is an active participant."""
        if not request.user.is_authenticated:
            return False

        # Import here to avoid circular imports
        from chat.models import Message

        # Handle Conversation, Participant, and Message objects
        if isinstance(obj, Participant):
            conversation = obj.conversation
        elif isinstance(obj, Message):
            conversation = obj.conversation
        else:
            conversation = obj

        return Participant.objects.filter(
            conversation=conversation,
            user=request.user,
            left_at__isnull=True,
        ).exists()


class IsConversationOwner(permissions.BasePermission):
    """
    Allows access only to the conversation owner.

    Used for destructive operations:
    - Deleting the conversation
    - Transferring ownership
    - Changing participant roles
    """

    message = "Only the conversation owner can perform this action."

    def has_object_permission(
        self, request: Request, view: APIView, obj: Conversation | Participant
    ) -> bool:
        """Check if user is the owner."""
        if not request.user.is_authenticated:
            return False

        # Handle both Conversation and Participant objects
        if isinstance(obj, Participant):
            conversation = obj.conversation
        else:
            conversation = obj

        return Participant.objects.filter(
            conversation=conversation,
            user=request.user,
            role=ParticipantRole.OWNER,
            left_at__isnull=True,
        ).exists()


class IsConversationAdminOrOwner(permissions.BasePermission):
    """
    Allows access to admins and owners.

    Used for management operations:
    - Updating group title
    - Adding participants
    - Removing members (not admins)
    """

    message = "Only admins or the owner can perform this action."

    def has_object_permission(
        self, request: Request, view: APIView, obj: Conversation | Participant
    ) -> bool:
        """Check if user is an admin or owner."""
        if not request.user.is_authenticated:
            return False

        # Handle both Conversation and Participant objects
        if isinstance(obj, Participant):
            conversation = obj.conversation
        else:
            conversation = obj

        return Participant.objects.filter(
            conversation=conversation,
            user=request.user,
            role__in=[ParticipantRole.OWNER, ParticipantRole.ADMIN],
            left_at__isnull=True,
        ).exists()


class CanManageParticipants(permissions.BasePermission):
    """
    Permission for adding/removing participants based on role hierarchy.

    Add participant:
        - OWNER can add anyone with any role (except OWNER)
        - ADMIN can add members only

    Remove participant:
        - OWNER can remove anyone (except self)
        - ADMIN can remove members only
        - Members cannot remove anyone

    This is used in conjunction with IsConversationParticipant.
    """

    message = "You don't have permission to manage this participant."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Check if user can manage participants at all."""
        if not request.user.is_authenticated:
            return False

        # For list/create, we need the conversation from the URL
        # The actual role check happens in has_object_permission
        return True

    def has_object_permission(
        self, request: Request, view: APIView, obj: Conversation | Participant
    ) -> bool:
        """Check if user can manage this specific participant."""
        if not request.user.is_authenticated:
            return False

        # Get conversation from object
        if isinstance(obj, Participant):
            conversation = obj.conversation
            target_participant = obj
        else:
            conversation = obj
            target_participant = None

        # Get current user's participant record
        try:
            actor = Participant.objects.get(
                conversation=conversation,
                user=request.user,
                left_at__isnull=True,
            )
        except Participant.DoesNotExist:
            return False

        # For creating participants (POST), check role in request data
        if request.method == "POST":
            requested_role = request.data.get("role", ParticipantRole.MEMBER)

            # OWNER can add admins or members
            if actor.role == ParticipantRole.OWNER:
                return requested_role in [ParticipantRole.ADMIN, ParticipantRole.MEMBER]

            # ADMIN can only add members
            if actor.role == ParticipantRole.ADMIN:
                return requested_role == ParticipantRole.MEMBER

            return False

        # For removing participants (DELETE)
        if request.method == "DELETE" and target_participant:
            # Cannot remove yourself via this endpoint (use leave)
            if target_participant.user == request.user:
                self.message = "Use the leave endpoint to remove yourself."
                return False

            # OWNER can remove anyone
            if actor.role == ParticipantRole.OWNER:
                return True

            # ADMIN can only remove members
            if actor.role == ParticipantRole.ADMIN:
                return target_participant.role == ParticipantRole.MEMBER

            return False

        # For updating participant role (PATCH)
        if request.method in ["PATCH", "PUT"] and target_participant:
            # Only owner can change roles
            return actor.role == ParticipantRole.OWNER

        return True


class CanModifyMessage(permissions.BasePermission):
    """
    Permission for modifying messages.

    Users can only delete their own messages.
    System messages cannot be deleted.
    """

    message = "You can only delete your own messages."

    def has_object_permission(self, request: Request, view: APIView, obj) -> bool:
        """Check if user can modify this message."""
        if not request.user.is_authenticated:
            return False

        # Import here to avoid circular imports
        from chat.models import Message, MessageType

        if not isinstance(obj, Message):
            return True

        # System messages cannot be deleted
        if obj.message_type == MessageType.SYSTEM:
            self.message = "System messages cannot be deleted."
            return False

        # Users can only delete their own messages
        return obj.sender == request.user


class IsGroupConversation(permissions.BasePermission):
    """
    Allows access only if the conversation is a group.

    Used for operations that only apply to groups:
    - Title updates
    - Participant management
    - Ownership transfer
    """

    message = "This action is only available for group conversations."

    def has_object_permission(
        self, request: Request, view: APIView, obj: Conversation | Participant
    ) -> bool:
        """Check if conversation is a group."""
        if isinstance(obj, Participant):
            conversation = obj.conversation
        else:
            conversation = obj

        return conversation.conversation_type == ConversationType.GROUP
