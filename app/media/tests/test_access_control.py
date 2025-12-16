"""
Tests for AccessControlService.

These tests verify:
- Permission hierarchy (NONE < VIEW < DOWNLOAD < EDIT < OWNER)
- Visibility-based access (PRIVATE, SHARED, INTERNAL)
- Share-based access with expiration
- Versioning interaction with access control

TDD: Write these tests first, then implement AccessControlService to pass them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone

from media.models import MediaFile
from media.services.access_control import AccessControlService, FileAccessLevel

if TYPE_CHECKING:
    from authentication.models import User


@pytest.mark.django_db
class TestAccessControlServiceOwnership:
    """Tests for owner access level."""

    def test_owner_has_owner_access_level(
        self,
        user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        File owner should have OWNER access level.

        Why it matters: Owners have full control over their files.
        """
        # The fixture creates file with `user` as uploader
        level = AccessControlService.get_access_level(user, media_file_for_processing)
        assert level == FileAccessLevel.OWNER

    def test_owner_can_access(
        self,
        user: "User",
        media_file_for_processing: MediaFile,
    ):
        """Owner should be able to access their file."""
        assert AccessControlService.user_can_access(user, media_file_for_processing)

    def test_owner_can_download(
        self,
        user: "User",
        media_file_for_processing: MediaFile,
    ):
        """Owner should be able to download their file."""
        assert AccessControlService.user_can_download(user, media_file_for_processing)

    def test_owner_can_edit(
        self,
        user: "User",
        media_file_for_processing: MediaFile,
    ):
        """Owner should be able to edit (create versions of) their file."""
        assert AccessControlService.user_can_edit(user, media_file_for_processing)


@pytest.mark.django_db
class TestAccessControlServiceUnauthenticated:
    """Tests for unauthenticated access."""

    def test_none_user_has_no_access(
        self,
        media_file_for_processing: MediaFile,
    ):
        """
        Unauthenticated user (None) should have no access.

        Why it matters: Security - anonymous access is not allowed.
        """
        level = AccessControlService.get_access_level(None, media_file_for_processing)
        assert level == FileAccessLevel.NONE

    def test_anonymous_user_has_no_access(
        self,
        media_file_for_processing: MediaFile,
    ):
        """
        AnonymousUser should have no access.

        Why it matters: Django's AnonymousUser should be treated as unauthenticated.
        """
        from django.contrib.auth.models import AnonymousUser

        anon = AnonymousUser()
        level = AccessControlService.get_access_level(anon, media_file_for_processing)
        assert level == FileAccessLevel.NONE

    def test_unauthenticated_cannot_access(
        self,
        media_file_for_processing: MediaFile,
    ):
        """Unauthenticated user cannot access any file."""
        assert not AccessControlService.user_can_access(None, media_file_for_processing)

    def test_unauthenticated_cannot_download(
        self,
        media_file_for_processing: MediaFile,
    ):
        """Unauthenticated user cannot download any file."""
        assert not AccessControlService.user_can_download(
            None, media_file_for_processing
        )


@pytest.mark.django_db
class TestAccessControlServicePrivateVisibility:
    """Tests for PRIVATE visibility files."""

    def test_other_user_has_no_access_to_private_file(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Non-owner should have no access to PRIVATE file.

        Why it matters: Private files are only accessible by owner.
        """
        # Ensure file is private
        media_file_for_processing.visibility = MediaFile.Visibility.PRIVATE
        media_file_for_processing.save()

        level = AccessControlService.get_access_level(
            other_user, media_file_for_processing
        )
        assert level == FileAccessLevel.NONE

    def test_staff_has_no_access_to_private_file(
        self,
        staff_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Staff should NOT have access to PRIVATE files.

        Why it matters: Private means private, even from staff.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.PRIVATE
        media_file_for_processing.save()

        level = AccessControlService.get_access_level(
            staff_user, media_file_for_processing
        )
        assert level == FileAccessLevel.NONE


@pytest.mark.django_db
class TestAccessControlServiceInternalVisibility:
    """Tests for INTERNAL visibility files."""

    def test_staff_has_view_access_to_internal_file(
        self,
        staff_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Staff should have VIEW access to INTERNAL files.

        Why it matters: Internal files are for staff/admin viewing.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.INTERNAL
        media_file_for_processing.save()

        level = AccessControlService.get_access_level(
            staff_user, media_file_for_processing
        )
        assert level == FileAccessLevel.VIEW

    def test_non_staff_has_no_access_to_internal_file(
        self,
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Non-staff should NOT have access to INTERNAL files.

        Why it matters: Internal files are restricted to staff.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.INTERNAL
        media_file_for_processing.save()

        level = AccessControlService.get_access_level(
            other_user, media_file_for_processing
        )
        assert level == FileAccessLevel.NONE

    def test_staff_can_access_internal_file(
        self,
        staff_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """Staff should be able to access internal files."""
        media_file_for_processing.visibility = MediaFile.Visibility.INTERNAL
        media_file_for_processing.save()

        assert AccessControlService.user_can_access(
            staff_user, media_file_for_processing
        )


@pytest.mark.django_db
class TestAccessControlServiceSharedVisibility:
    """Tests for SHARED visibility files with explicit shares."""

    def test_shared_user_with_view_only_has_view_access(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        User with view-only share should have VIEW access level.

        Why it matters: Share grants specific access level.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share with view-only (can_download=False)
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=False,
        )

        level = AccessControlService.get_access_level(
            other_user, media_file_for_processing
        )
        assert level == FileAccessLevel.VIEW

    def test_shared_user_with_download_has_download_access(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        User with download share should have DOWNLOAD access level.

        Why it matters: Share grants specific access level.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        level = AccessControlService.get_access_level(
            other_user, media_file_for_processing
        )
        assert level == FileAccessLevel.DOWNLOAD

    def test_non_shared_user_has_no_access_to_shared_file(
        self,
        third_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        User without share should have no access to SHARED file.

        Why it matters: SHARED doesn't mean public, only explicitly shared users.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        level = AccessControlService.get_access_level(
            third_user, media_file_for_processing
        )
        assert level == FileAccessLevel.NONE

    def test_view_only_access_cannot_download(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        User with VIEW-only access cannot download.

        Why it matters: VIEW is read-only metadata access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=False,
        )

        assert not AccessControlService.user_can_download(
            other_user, media_file_for_processing
        )

    def test_download_access_can_download(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        User with DOWNLOAD access can download.

        Why it matters: DOWNLOAD grants file download permission.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        assert AccessControlService.user_can_download(
            other_user, media_file_for_processing
        )

    def test_shared_user_cannot_edit(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Shared user (even with download) cannot edit.

        Why it matters: Edit is owner-only.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        assert not AccessControlService.user_can_edit(
            other_user, media_file_for_processing
        )


@pytest.mark.django_db
class TestAccessControlServiceShareExpiration:
    """Tests for share expiration handling."""

    def test_expired_share_returns_no_access(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Expired share should return NONE access.

        Why it matters: Expiration enforces time-limited access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share then manually expire it
        result = AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
            expires_in_days=1,
        )

        # Manually set expiration to the past
        share = result.data
        share.expires_at = timezone.now() - timedelta(hours=1)
        share.save()

        level = AccessControlService.get_access_level(
            other_user, media_file_for_processing
        )
        assert level == FileAccessLevel.NONE

    def test_non_expired_share_returns_access(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Non-expired share should return the granted access level.

        Why it matters: Valid shares grant access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share expiring in the future
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
            expires_in_days=7,
        )

        level = AccessControlService.get_access_level(
            other_user, media_file_for_processing
        )
        assert level == FileAccessLevel.DOWNLOAD

    def test_share_without_expiration_returns_access(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Share without expiration should return access indefinitely.

        Why it matters: Permanent shares are valid.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share without expiration
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
            expires_in_days=None,  # No expiration
        )

        level = AccessControlService.get_access_level(
            other_user, media_file_for_processing
        )
        assert level == FileAccessLevel.DOWNLOAD


@pytest.mark.django_db
class TestAccessControlServiceShareManagement:
    """Tests for share creation and revocation."""

    def test_share_file_creates_share(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        share_file should create a MediaFileShare record.

        Why it matters: Shares must be persisted.
        """
        result = AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=False,
        )

        assert result.success
        assert result.data is not None
        assert result.data.shared_with == other_user
        assert result.data.can_download is False

    def test_share_file_sets_visibility_to_shared(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        share_file should set visibility to SHARED if PRIVATE.

        Why it matters: Visibility should reflect sharing state.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.PRIVATE
        media_file_for_processing.save()

        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        media_file_for_processing.refresh_from_db()
        assert media_file_for_processing.visibility == MediaFile.Visibility.SHARED

    def test_share_file_non_owner_fails(
        self,
        other_user: "User",
        third_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        share_file should fail if caller is not owner.

        Why it matters: Only owners can share files.
        """
        result = AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=other_user,  # Not the owner
            recipient=third_user,
            can_download=True,
        )

        assert not result.success
        assert result.error_code == "NOT_OWNER"

    def test_share_file_with_self_fails(
        self,
        user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        share_file should fail if sharing with self.

        Why it matters: Owner already has access, self-sharing is redundant.
        """
        result = AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=user,  # Same as owner
            can_download=True,
        )

        assert not result.success
        assert result.error_code == "SELF_SHARE"

    def test_share_file_updates_existing_share(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        share_file should update existing share instead of creating duplicate.

        Why it matters: Unique constraint on (media_file, shared_with).
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create initial share with view-only
        result1 = AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=False,
        )

        # Update to download
        result2 = AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        assert result1.success
        assert result2.success
        # Should be same share record, updated
        assert result1.data.id == result2.data.id
        assert result2.data.can_download is True

    def test_revoke_share_removes_access(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        revoke_share should remove user's access.

        Why it matters: Owners can revoke access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        # Verify access exists
        assert AccessControlService.user_can_download(
            other_user, media_file_for_processing
        )

        # Revoke
        result = AccessControlService.revoke_share(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
        )

        assert result.success

        # Verify access removed
        assert not AccessControlService.user_can_access(
            other_user, media_file_for_processing
        )

    def test_revoke_share_sets_visibility_to_private_if_no_shares(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        revoke_share should set visibility to PRIVATE if no shares remain.

        Why it matters: Visibility should reflect sharing state.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create and revoke share
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        AccessControlService.revoke_share(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
        )

        media_file_for_processing.refresh_from_db()
        assert media_file_for_processing.visibility == MediaFile.Visibility.PRIVATE

    def test_revoke_share_non_owner_fails(
        self,
        user: "User",
        other_user: "User",
        third_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        revoke_share should fail if caller is not owner.

        Why it matters: Only owners can revoke shares.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share as owner
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        # Try to revoke as non-owner
        result = AccessControlService.revoke_share(
            media_file=media_file_for_processing,
            owner=third_user,  # Not the owner
            recipient=other_user,
        )

        assert not result.success
        assert result.error_code == "NOT_OWNER"


@pytest.mark.django_db
class TestAccessControlServiceVersioning:
    """Tests for versioning interaction with access control."""

    def test_share_applies_to_all_versions(
        self,
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
        sample_jpeg_uploaded,
    ):
        """
        Share on a file should apply to all versions in the group.

        Why it matters: Users shouldn't need separate shares per version.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create a new version
        new_version = media_file_for_processing.create_new_version(
            new_file=sample_jpeg_uploaded,
            requesting_user=user,
        )

        # Share original
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        # Both versions should be accessible
        assert AccessControlService.user_can_download(
            other_user, media_file_for_processing
        )
        assert AccessControlService.user_can_download(other_user, new_version)


@pytest.mark.django_db
class TestAccessControlServiceSoftDelete:
    """Tests for soft-deleted file access."""

    def test_soft_deleted_file_no_access_for_non_owner(
        self,
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Soft-deleted files should not be accessible by non-owners.

        Why it matters: Deleted files should be hidden.
        """
        media_file_for_processing.soft_delete()

        level = AccessControlService.get_access_level(
            other_user, media_file_for_processing
        )
        assert level == FileAccessLevel.NONE

    def test_soft_deleted_file_owner_still_has_access(
        self,
        user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Soft-deleted files should still be accessible by owner.

        Why it matters: Owners need access to restore deleted files.
        """
        media_file_for_processing.soft_delete()

        level = AccessControlService.get_access_level(user, media_file_for_processing)
        assert level == FileAccessLevel.OWNER
