"""
AccessControlService for centralized media file permission management.

Provides:
- Access level hierarchy (NONE < VIEW < DOWNLOAD < EDIT < OWNER)
- Visibility-based access (private, shared, internal)
- Explicit share grants with expiration
- Version group-aware permissions
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import IntEnum
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.services import BaseService, ServiceResult
from media.models import MediaFile, MediaFileShare

if TYPE_CHECKING:
    from authentication.models import User


class FileAccessLevel(IntEnum):
    """
    Access levels for media files.

    Ordered hierarchy - higher values include all lower permissions.
    NONE < VIEW < DOWNLOAD < EDIT < OWNER

    VIEW: Can see file metadata and preview
    DOWNLOAD: Can download the file
    EDIT: Can modify file metadata, create versions
    OWNER: Full control including delete and share management
    """

    NONE = 0
    VIEW = 1
    DOWNLOAD = 2
    EDIT = 3
    OWNER = 4


class AccessControlService(BaseService):
    """
    Centralized service for media file access control.

    All permission checks should go through this service to ensure
    consistent enforcement of the access control model.

    Access Model:
    - Ownership: Owner always has OWNER access
    - Visibility: INTERNAL files grant VIEW to staff
    - Shares: Explicit grants to specific users with optional expiration
    - Version Groups: Shares apply to all versions in a group

    Usage:
        # Check access level
        level = AccessControlService.get_access_level(user, media_file)
        if level >= FileAccessLevel.DOWNLOAD:
            serve_file(media_file)

        # Boolean checks
        if AccessControlService.user_can_download(user, media_file):
            serve_file(media_file)

        # Create a share
        result = AccessControlService.share_file(
            media_file=media_file,
            owner=owner_user,
            recipient=other_user,
            can_download=True,
            expires_in_days=7,
        )
    """

    @classmethod
    def get_access_level(
        cls,
        user: User | None,
        media_file: MediaFile,
    ) -> FileAccessLevel:
        """
        Determine the access level a user has for a media file.

        Evaluates access in order:
        1. Owner always has OWNER access
        2. Staff have VIEW access to INTERNAL files
        3. Explicit shares grant VIEW or DOWNLOAD based on can_download
        4. No match means NONE

        Args:
            user: The user requesting access (None for anonymous)
            media_file: The media file to check

        Returns:
            FileAccessLevel indicating what the user can do
        """
        # Anonymous users have no access
        if user is None:
            return FileAccessLevel.NONE

        # Handle Django's AnonymousUser
        if not getattr(user, "is_authenticated", False):
            return FileAccessLevel.NONE

        # Owner always has full access
        if cls._is_owner(user, media_file):
            return FileAccessLevel.OWNER

        # Staff can view internal files
        if media_file.visibility == MediaFile.Visibility.INTERNAL and user.is_staff:
            return FileAccessLevel.VIEW

        # Check for explicit share (applies to version group)
        share = cls._get_valid_share(user, media_file)
        if share:
            return (
                FileAccessLevel.DOWNLOAD if share.can_download else FileAccessLevel.VIEW
            )

        # No access
        return FileAccessLevel.NONE

    @classmethod
    def user_can_access(cls, user: User | None, media_file: MediaFile) -> bool:
        """
        Check if user has at least VIEW access.

        Args:
            user: The user requesting access
            media_file: The media file to check

        Returns:
            True if user can at least view the file
        """
        return cls.get_access_level(user, media_file) >= FileAccessLevel.VIEW

    @classmethod
    def user_can_download(cls, user: User | None, media_file: MediaFile) -> bool:
        """
        Check if user has DOWNLOAD access or higher.

        Args:
            user: The user requesting access
            media_file: The media file to check

        Returns:
            True if user can download the file
        """
        return cls.get_access_level(user, media_file) >= FileAccessLevel.DOWNLOAD

    @classmethod
    def user_can_edit(cls, user: User | None, media_file: MediaFile) -> bool:
        """
        Check if user has EDIT access or higher.

        Currently only owners can edit files.

        Args:
            user: The user requesting access
            media_file: The media file to check

        Returns:
            True if user can edit the file
        """
        return cls.get_access_level(user, media_file) >= FileAccessLevel.EDIT

    @classmethod
    def share_file(
        cls,
        media_file: MediaFile,
        owner: User,
        recipient: User,
        can_download: bool = True,
        expires_in_days: int | None = None,
        message: str | None = None,
    ) -> ServiceResult[MediaFileShare]:
        """
        Create a share grant for a media file.

        Shares are always created on the version_group root to ensure
        they apply to all versions.

        Args:
            media_file: The file to share (will use version_group root)
            owner: User creating the share (must be file owner)
            recipient: User receiving access
            can_download: Whether recipient can download (vs view-only)
            expires_in_days: Days until share expires (None = never)
            message: Optional message to recipient

        Returns:
            ServiceResult with created MediaFileShare or error
        """
        # Verify owner
        if not cls._is_owner(owner, media_file):
            return ServiceResult.failure(
                "Only the file owner can share this file",
                error_code="NOT_OWNER",
            )

        # Can't share with yourself
        if owner.id == recipient.id:
            return ServiceResult.failure(
                "Cannot share a file with yourself",
                error_code="SELF_SHARE",
            )

        # Get the version group root for consistent sharing
        version_group_root = media_file.version_group

        # Calculate expiration
        expires_at = None
        if expires_in_days is not None:
            expires_at = timezone.now() + timedelta(days=expires_in_days)

        with transaction.atomic():
            # Check for existing share
            existing = MediaFileShare.objects.filter(
                media_file=version_group_root,
                shared_with=recipient,
            ).first()

            if existing:
                # Update existing share
                existing.can_download = can_download
                existing.expires_at = expires_at
                existing.message = message
                existing.save()
                share = existing
            else:
                # Create new share
                share = MediaFileShare.objects.create(
                    media_file=version_group_root,
                    shared_by=owner,
                    shared_with=recipient,
                    can_download=can_download,
                    expires_at=expires_at,
                    message=message,
                )

            # Update visibility to SHARED if currently PRIVATE
            if version_group_root.visibility == MediaFile.Visibility.PRIVATE:
                version_group_root.visibility = MediaFile.Visibility.SHARED
                version_group_root.save(update_fields=["visibility", "updated_at"])

        cls.get_logger().info(
            f"File {version_group_root.id} shared with user {recipient.id} "
            f"(can_download={can_download}, expires_at={expires_at})"
        )

        return ServiceResult.success(share)

    @classmethod
    def revoke_share(
        cls,
        media_file: MediaFile,
        owner: User,
        recipient: User,
    ) -> ServiceResult[bool]:
        """
        Revoke a share grant.

        If this was the last share, visibility reverts to PRIVATE.

        Args:
            media_file: The file (will use version_group root)
            owner: User revoking the share (must be file owner)
            recipient: User whose access is being revoked

        Returns:
            ServiceResult with True on success
        """
        # Verify owner
        if not cls._is_owner(owner, media_file):
            return ServiceResult.failure(
                "Only the file owner can revoke shares",
                error_code="NOT_OWNER",
            )

        version_group_root = media_file.version_group

        with transaction.atomic():
            deleted_count, _ = MediaFileShare.objects.filter(
                media_file=version_group_root,
                shared_with=recipient,
            ).delete()

            if deleted_count == 0:
                return ServiceResult.failure(
                    "No share found for this user",
                    error_code="SHARE_NOT_FOUND",
                )

            # If no shares remain and visibility is SHARED, revert to PRIVATE
            remaining_shares = MediaFileShare.objects.filter(
                media_file=version_group_root
            ).exists()

            if (
                not remaining_shares
                and version_group_root.visibility == MediaFile.Visibility.SHARED
            ):
                version_group_root.visibility = MediaFile.Visibility.PRIVATE
                version_group_root.save(update_fields=["visibility", "updated_at"])

        cls.get_logger().info(
            f"Share revoked: file {version_group_root.id} from user {recipient.id}"
        )

        return ServiceResult.success(True)

    @classmethod
    def get_file_shares(cls, media_file: MediaFile) -> list[MediaFileShare]:
        """
        Get all active (non-expired) shares for a file.

        Args:
            media_file: The media file

        Returns:
            List of active MediaFileShare objects
        """
        now = timezone.now()
        return list(
            MediaFileShare.objects.filter(media_file=media_file.version_group)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .select_related("shared_with")
        )

    @classmethod
    def get_files_shared_with_user(cls, user: User) -> list[MediaFile]:
        """
        Get all files currently shared with a user.

        Returns files where the user has an active (non-expired) share.

        Args:
            user: The user to check

        Returns:
            List of MediaFile objects shared with the user
        """
        now = timezone.now()
        share_file_ids = (
            MediaFileShare.objects.filter(shared_with=user)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .values_list("media_file_id", flat=True)
        )

        # Return current versions of shared files
        return list(
            MediaFile.objects.filter(
                version_group_id__in=share_file_ids,
                is_current=True,
            ).select_related("uploader")
        )

    @classmethod
    def update_share(
        cls,
        media_file: MediaFile,
        owner: User,
        recipient: User,
        can_download: bool | None = None,
        expires_at: datetime | None = None,
        clear_expiration: bool = False,
    ) -> ServiceResult[MediaFileShare]:
        """
        Update an existing share.

        Args:
            media_file: The file (will use version_group root)
            owner: User updating the share (must be file owner)
            recipient: User whose share is being updated
            can_download: New download permission (None = no change)
            expires_at: New expiration datetime (None = no change)
            clear_expiration: If True, removes expiration (never expires)

        Returns:
            ServiceResult with updated MediaFileShare
        """
        # Verify owner
        if not cls._is_owner(owner, media_file):
            return ServiceResult.failure(
                "Only the file owner can update shares",
                error_code="NOT_OWNER",
            )

        version_group_root = media_file.version_group

        share = MediaFileShare.objects.filter(
            media_file=version_group_root,
            shared_with=recipient,
        ).first()

        if not share:
            return ServiceResult.failure(
                "No share found for this user",
                error_code="SHARE_NOT_FOUND",
            )

        if can_download is not None:
            share.can_download = can_download

        if clear_expiration:
            share.expires_at = None
        elif expires_at is not None:
            share.expires_at = expires_at

        share.save()

        cls.get_logger().info(
            f"Share updated: file {version_group_root.id} for user {recipient.id}"
        )

        return ServiceResult.success(share)

    # =========================================================================
    # Private Helper Methods
    # =========================================================================

    @classmethod
    def _is_owner(cls, user: User, media_file: MediaFile) -> bool:
        """Check if user is the owner of the file's version group."""
        return media_file.version_group.uploader_id == user.id

    @classmethod
    def _get_valid_share(
        cls,
        user: User,
        media_file: MediaFile,
    ) -> MediaFileShare | None:
        """
        Get a valid (non-expired) share for the user.

        Args:
            user: The user to check
            media_file: The media file

        Returns:
            MediaFileShare if valid share exists, None otherwise
        """
        now = timezone.now()
        return (
            MediaFileShare.objects.filter(
                media_file=media_file.version_group,
                shared_with=user,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .first()
        )
