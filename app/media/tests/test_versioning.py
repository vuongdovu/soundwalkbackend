"""
Tests for MediaFile versioning functionality.

These tests verify:
- Factory method sets version_group to self for originals
- create_new_version() creates properly linked versions
- Version history retrieval and ordering
- Database constraints for versioning integrity
- Ownership and quota enforcement

TDD: Write these tests first, then implement model changes to pass them.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from authentication.tests.factories import UserFactory
from media.models import MediaFile


# =============================================================================
# Factory Method Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileVersioningFactory:
    """Tests for factory method versioning behavior."""

    def test_create_from_upload_sets_version_group_to_self(
        self, user, sample_jpeg_uploaded
    ):
        """
        Original files should have version_group pointing to themselves.

        Why it matters: The self-referential pattern enables database constraints
        that ensure only one current version per group.
        """
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        assert media_file.version_group_id == media_file.pk
        assert media_file.version_group == media_file

    def test_create_from_upload_sets_version_to_one(self, user, sample_jpeg_uploaded):
        """
        Original files should have version=1.

        Why it matters: Version numbering starts at 1 for the original.
        """
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        assert media_file.version == 1

    def test_create_from_upload_sets_is_current_true(self, user, sample_jpeg_uploaded):
        """
        Original files should have is_current=True.

        Why it matters: Newly uploaded files are always the current version.
        """
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        assert media_file.is_current is True

    def test_direct_create_sets_version_group_to_self(self, user):
        """
        Direct MediaFile.objects.create() should auto-set version_group.

        Why it matters: Backward compatibility - existing code using
        direct create() should still work after versioning is implemented.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )

        # version_group should be auto-populated to self
        media_file.refresh_from_db()
        assert media_file.version_group_id == media_file.pk


# =============================================================================
# create_new_version() Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileCreateNewVersion:
    """Tests for create_new_version() method."""

    def test_create_new_version_increments_version(self, user, sample_jpeg_uploaded):
        """
        New version should have version = max(group) + 1.

        Why it matters: Version numbers must be sequential within a group.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        # Create new version
        new_file = SimpleUploadedFile(
            "test_v2.jpg", b"version 2 content", content_type="image/jpeg"
        )
        new_version = original.create_new_version(new_file, user)

        assert new_version.version == 2

    def test_create_new_version_marks_previous_not_current(
        self, user, sample_jpeg_uploaded
    ):
        """
        Creating new version marks all prior versions as not current.

        Why it matters: Only one version should be current at any time.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        # Create new version
        new_file = SimpleUploadedFile(
            "test_v2.jpg", b"version 2 content", content_type="image/jpeg"
        )
        original.create_new_version(new_file, user)

        # Refresh original from database
        original.refresh_from_db()
        assert original.is_current is False

    def test_create_new_version_shares_version_group(self, user, sample_jpeg_uploaded):
        """
        New version should share version_group with original.

        Why it matters: All versions must belong to the same group.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        new_file = SimpleUploadedFile(
            "test_v2.jpg", b"version 2 content", content_type="image/jpeg"
        )
        new_version = original.create_new_version(new_file, user)

        assert new_version.version_group_id == original.version_group_id
        assert new_version.version_group_id == original.pk

    def test_create_new_version_sets_is_current_true(self, user, sample_jpeg_uploaded):
        """
        New version should be marked as current.

        Why it matters: The newest version is always the current one.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        new_file = SimpleUploadedFile(
            "test_v2.jpg", b"version 2 content", content_type="image/jpeg"
        )
        new_version = original.create_new_version(new_file, user)

        assert new_version.is_current is True

    def test_create_new_version_preserves_uploader(self, user, sample_jpeg_uploaded):
        """
        New version should have same uploader as original.

        Why it matters: Version ownership should be consistent.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        new_file = SimpleUploadedFile(
            "test_v2.jpg", b"version 2 content", content_type="image/jpeg"
        )
        new_version = original.create_new_version(new_file, user)

        assert new_version.uploader_id == original.uploader_id
        assert new_version.uploader == user

    def test_create_new_version_preserves_media_type(self, user, sample_jpeg_uploaded):
        """
        New version should inherit media_type from original.

        Why it matters: All versions should be of the same type.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        new_file = SimpleUploadedFile(
            "test_v2.jpg", b"version 2 content", content_type="image/jpeg"
        )
        new_version = original.create_new_version(new_file, user)

        assert new_version.media_type == original.media_type

    def test_create_new_version_preserves_visibility(self, user, sample_jpeg_uploaded):
        """
        New version should inherit visibility from original.

        Why it matters: Sharing settings should persist across versions.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
            visibility="shared",
        )

        new_file = SimpleUploadedFile(
            "test_v2.jpg", b"version 2 content", content_type="image/jpeg"
        )
        new_version = original.create_new_version(new_file, user)

        assert new_version.visibility == "shared"

    def test_create_multiple_versions(self, user, sample_jpeg_uploaded):
        """
        Should be able to create multiple versions sequentially.

        Why it matters: Files may go through many revisions.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        # Create version 2
        file_v2 = SimpleUploadedFile(
            "test_v2.jpg", b"version 2", content_type="image/jpeg"
        )
        v2 = original.create_new_version(file_v2, user)

        # Create version 3 from v2
        file_v3 = SimpleUploadedFile(
            "test_v3.jpg", b"version 3", content_type="image/jpeg"
        )
        v3 = v2.create_new_version(file_v3, user)

        assert v3.version == 3
        assert v3.is_current is True

        # All previous versions should not be current
        original.refresh_from_db()
        v2.refresh_from_db()
        assert original.is_current is False
        assert v2.is_current is False

        # All should share same version_group
        assert original.version_group_id == v2.version_group_id == v3.version_group_id


# =============================================================================
# Ownership Enforcement Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileVersioningOwnership:
    """Tests for ownership enforcement in versioning."""

    def test_create_new_version_by_non_owner_raises_error(
        self, user, sample_jpeg_uploaded
    ):
        """
        Non-owner cannot create new version.

        Why it matters: Only the original uploader should be able to version.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        # Create a different user
        other_user = UserFactory(email_verified=True)

        new_file = SimpleUploadedFile(
            "test_v2.jpg", b"version 2 content", content_type="image/jpeg"
        )

        with pytest.raises(PermissionError) as exc_info:
            original.create_new_version(new_file, other_user)

        assert "Only the original uploader" in str(exc_info.value)


# =============================================================================
# Quota Enforcement Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileVersioningQuota:
    """Tests for quota enforcement in versioning."""

    def test_create_new_version_checks_quota(
        self, user_near_quota, sample_jpeg_uploaded
    ):
        """
        Should raise ValidationError if quota exceeded.

        Why it matters: Versioning should respect storage limits.
        """
        # user_near_quota has only ~1MB remaining
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user_near_quota,
            media_type="image",
            mime_type="image/jpeg",
        )
        # Update storage usage to account for the upload
        user_near_quota.profile.add_storage_usage(sample_jpeg_uploaded.size)
        sample_jpeg_uploaded.seek(0)

        # Create a large file that exceeds remaining quota
        large_content = b"x" * (2 * 1024 * 1024)  # 2MB
        new_file = SimpleUploadedFile(
            "large_v2.jpg", large_content, content_type="image/jpeg"
        )

        with pytest.raises(ValidationError) as exc_info:
            original.create_new_version(new_file, user_near_quota)

        assert "Storage quota exceeded" in str(exc_info.value)

    def test_create_new_version_updates_quota(self, user, sample_jpeg_uploaded):
        """
        Should increment user's storage usage after successful version.

        Why it matters: Storage tracking must be accurate.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        initial_storage = user.profile.total_storage_bytes

        new_file = SimpleUploadedFile(
            "test_v2.jpg",
            b"version 2 content with more data",
            content_type="image/jpeg",
        )
        new_version = original.create_new_version(new_file, user)

        user.profile.refresh_from_db()
        assert (
            user.profile.total_storage_bytes == initial_storage + new_version.file_size
        )


# =============================================================================
# get_version_history() Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileVersionHistory:
    """Tests for get_version_history() method."""

    def test_get_version_history_returns_all_versions(self, user, sample_jpeg_uploaded):
        """
        Should return all versions in the group.

        Why it matters: Users need to see complete version history.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        file_v2 = SimpleUploadedFile(
            "test_v2.jpg", b"version 2", content_type="image/jpeg"
        )
        v2 = original.create_new_version(file_v2, user)

        file_v3 = SimpleUploadedFile(
            "test_v3.jpg", b"version 3", content_type="image/jpeg"
        )
        v3 = v2.create_new_version(file_v3, user)

        history = original.get_version_history()

        assert history.count() == 3
        assert set(history.values_list("pk", flat=True)) == {original.pk, v2.pk, v3.pk}

    def test_get_version_history_ordered_by_version_desc(
        self, user, sample_jpeg_uploaded
    ):
        """
        Should return versions ordered by version number descending.

        Why it matters: Most recent version should appear first.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        file_v2 = SimpleUploadedFile(
            "test_v2.jpg", b"version 2", content_type="image/jpeg"
        )
        v2 = original.create_new_version(file_v2, user)

        file_v3 = SimpleUploadedFile(
            "test_v3.jpg", b"version 3", content_type="image/jpeg"
        )
        v2.create_new_version(file_v3, user)

        history = list(original.get_version_history())

        assert history[0].version == 3
        assert history[1].version == 2
        assert history[2].version == 1

    def test_get_version_history_from_any_version(self, user, sample_jpeg_uploaded):
        """
        Should work when called from any version in the group.

        Why it matters: API might access history from any version.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        file_v2 = SimpleUploadedFile(
            "test_v2.jpg", b"version 2", content_type="image/jpeg"
        )
        v2 = original.create_new_version(file_v2, user)

        # Get history from v2 (not original)
        history = v2.get_version_history()

        assert history.count() == 2


# =============================================================================
# is_original Property Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileIsOriginal:
    """Tests for is_original property."""

    def test_is_original_true_for_version_one(self, user, sample_jpeg_uploaded):
        """
        Files with version=1 should have is_original=True.

        Why it matters: Identifies the first upload in a version chain.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        assert original.is_original is True

    def test_is_original_false_for_later_versions(self, user, sample_jpeg_uploaded):
        """
        Files with version>1 should have is_original=False.

        Why it matters: Distinguishes subsequent versions from the original.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        new_file = SimpleUploadedFile(
            "test_v2.jpg", b"version 2 content", content_type="image/jpeg"
        )
        v2 = original.create_new_version(new_file, user)

        assert v2.is_original is False


# =============================================================================
# Database Constraint Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileVersioningConstraints:
    """Tests for database constraints on versioning."""

    def test_unique_current_version_per_group(self, user, sample_jpeg_uploaded):
        """
        Only one file per version_group can have is_current=True.

        Why it matters: Prevents data inconsistency.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        # Try to create another file with same version_group and is_current=True
        file_content = SimpleUploadedFile(
            "test_v2.txt", b"test content", content_type="text/plain"
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                MediaFile.objects.create(
                    file=file_content,
                    original_filename="test_v2.txt",
                    media_type=MediaFile.MediaType.DOCUMENT,
                    mime_type="text/plain",
                    file_size=12,
                    uploader=user,
                    version_group=original,
                    version=2,
                    is_current=True,  # Violates constraint - original is also current
                )

    def test_unique_version_number_per_group(self, user, sample_jpeg_uploaded):
        """
        Version numbers must be unique within a version_group.

        Why it matters: Prevents duplicate version numbers.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        # Try to create another file with same version_group and version=1
        file_content = SimpleUploadedFile(
            "test_duplicate.txt", b"test content", content_type="text/plain"
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                MediaFile.objects.create(
                    file=file_content,
                    original_filename="test_duplicate.txt",
                    media_type=MediaFile.MediaType.DOCUMENT,
                    mime_type="text/plain",
                    file_size=12,
                    uploader=user,
                    version_group=original,
                    version=1,  # Violates constraint - same version as original
                    is_current=False,
                )

    def test_version_group_cascade_deletes_versions(self, user, sample_jpeg_uploaded):
        """
        Hard deleting version_group root should delete all versions.

        Why it matters: No orphaned versions should exist.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        file_v2 = SimpleUploadedFile(
            "test_v2.jpg", b"version 2", content_type="image/jpeg"
        )
        v2 = original.create_new_version(file_v2, user)
        v2_pk = v2.pk

        # Hard delete the original (version_group root)
        # Use all_objects to bypass soft delete manager
        MediaFile.all_objects.filter(pk=original.pk).delete()

        # v2 should also be deleted (CASCADE)
        assert not MediaFile.all_objects.filter(pk=v2_pk).exists()


# =============================================================================
# Soft Delete Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileVersioningSoftDelete:
    """Tests for soft delete behavior with versioning."""

    def test_soft_delete_only_affects_specific_version(
        self, user, sample_jpeg_uploaded
    ):
        """
        Soft deleting a version should not affect other versions.

        Why it matters: User decision was per-version soft delete, not per-group.
        """
        original = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        file_v2 = SimpleUploadedFile(
            "test_v2.jpg", b"version 2", content_type="image/jpeg"
        )
        v2 = original.create_new_version(file_v2, user)

        # Soft delete v2
        v2.is_deleted = True
        v2.save()

        # Original should not be affected
        original.refresh_from_db()
        assert original.is_deleted is False
