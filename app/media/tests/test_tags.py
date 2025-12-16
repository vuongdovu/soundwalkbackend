"""
Tests for Tag and MediaFileTag models.

These tests verify:
- Tag creation with different types (user, system, auto)
- Hybrid scoping (user tags per-user, global tags unique)
- MediaFileTag associations and constraints
- Convenience methods on MediaFile
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from PIL import Image

from authentication.tests.factories import UserFactory
from media.models import MediaFile, MediaFileTag, Tag

if TYPE_CHECKING:
    from authentication.models import User


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def user(db) -> "User":
    """Create a verified user with default storage quota."""
    user = UserFactory(email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB
    user.profile.total_storage_bytes = 0
    user.profile.save()
    return user


@pytest.fixture
def other_user(db) -> "User":
    """Create another verified user."""
    user = UserFactory(email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB
    user.profile.total_storage_bytes = 0
    user.profile.save()
    return user


@pytest.fixture
def sample_jpeg_file() -> SimpleUploadedFile:
    """Generate a valid JPEG image as SimpleUploadedFile."""
    image = Image.new("RGB", (100, 100), color="red")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    return SimpleUploadedFile(
        name="test_image.jpg",
        content=buffer.read(),
        content_type="image/jpeg",
    )


@pytest.fixture
def media_file(user, sample_jpeg_file) -> MediaFile:
    """Create a media file for tag testing."""
    return MediaFile.create_from_upload(
        file=sample_jpeg_file,
        uploader=user,
        media_type="image",
        mime_type="image/jpeg",
    )


# =============================================================================
# Tag Model Tests
# =============================================================================


@pytest.mark.django_db
class TestTagCreation:
    """Tests for Tag model creation."""

    def test_create_user_tag(self, user):
        """User tags should be created with an owner."""
        tag = Tag.objects.create(
            name="Vacation",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        assert tag.name == "Vacation"
        assert tag.slug == "vacation"
        assert tag.tag_type == Tag.TagType.USER
        assert tag.owner == user
        assert tag.is_user_tag is True
        assert tag.is_system_tag is False
        assert tag.is_auto_tag is False

    def test_create_system_tag(self):
        """System tags should be created without an owner."""
        tag = Tag.objects.create(
            name="Featured",
            tag_type=Tag.TagType.SYSTEM,
        )

        assert tag.name == "Featured"
        assert tag.slug == "featured"
        assert tag.tag_type == Tag.TagType.SYSTEM
        assert tag.owner is None
        assert tag.is_system_tag is True

    def test_create_auto_tag(self):
        """Auto tags should be created without an owner."""
        tag = Tag.objects.create(
            name="Sunset",
            tag_type=Tag.TagType.AUTO,
            category="scene",
        )

        assert tag.name == "Sunset"
        assert tag.tag_type == Tag.TagType.AUTO
        assert tag.category == "scene"
        assert tag.owner is None
        assert tag.is_auto_tag is True

    def test_auto_slug_generation(self, user):
        """Slug should be auto-generated from name."""
        tag = Tag.objects.create(
            name="My Favorite Photos",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        assert tag.slug == "my-favorite-photos"

    def test_custom_slug_preserved(self, user):
        """Custom slug should not be overwritten."""
        tag = Tag.objects.create(
            name="Vacation",
            slug="holidays-2024",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        assert tag.slug == "holidays-2024"

    def test_get_or_create_user_tag(self, user):
        """get_or_create_user_tag should create or return existing tag."""
        tag1, created1 = Tag.get_or_create_user_tag(
            name="Work",
            owner=user,
            color="#FF5733",
        )
        assert created1 is True

        tag2, created2 = Tag.get_or_create_user_tag(
            name="Work",
            owner=user,
        )
        assert created2 is False
        assert tag1.pk == tag2.pk

    def test_get_or_create_auto_tag(self):
        """get_or_create_auto_tag should create or return existing tag."""
        tag1, created1 = Tag.get_or_create_auto_tag(
            name="Beach",
            category="location",
        )
        assert created1 is True

        tag2, created2 = Tag.get_or_create_auto_tag(
            name="Beach",
        )
        assert created2 is False
        assert tag1.pk == tag2.pk


@pytest.mark.django_db
class TestTagHybridScoping:
    """Tests for hybrid tag scoping."""

    def test_same_name_different_users(self, user, other_user):
        """Different users can have tags with the same name."""
        tag1 = Tag.objects.create(
            name="Favorites",
            tag_type=Tag.TagType.USER,
            owner=user,
        )
        tag2 = Tag.objects.create(
            name="Favorites",
            tag_type=Tag.TagType.USER,
            owner=other_user,
        )

        assert tag1.pk != tag2.pk
        assert tag1.slug == tag2.slug == "favorites"

    def test_duplicate_user_tag_same_owner_fails(self, user):
        """Same user cannot have duplicate tag slugs."""
        Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        with pytest.raises(IntegrityError):
            Tag.objects.create(
                name="Work",  # Same slug
                tag_type=Tag.TagType.USER,
                owner=user,
            )

    def test_duplicate_global_tag_fails(self):
        """Global tags (system/auto) cannot have duplicate slugs."""
        Tag.objects.create(
            name="Featured",
            tag_type=Tag.TagType.SYSTEM,
        )

        with pytest.raises(IntegrityError):
            Tag.objects.create(
                name="Featured",  # Same slug
                tag_type=Tag.TagType.AUTO,
            )

    def test_user_tag_and_global_tag_same_slug_allowed(self, user):
        """User can have same slug as a global tag."""
        global_tag = Tag.objects.create(
            name="Featured",
            tag_type=Tag.TagType.SYSTEM,
        )
        user_tag = Tag.objects.create(
            name="Featured",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        assert global_tag.pk != user_tag.pk
        assert global_tag.slug == user_tag.slug


@pytest.mark.django_db
class TestTagConstraints:
    """Tests for Tag model constraints."""

    def test_user_tag_must_have_owner(self):
        """User tags without owner should violate constraint."""
        with pytest.raises(IntegrityError):
            Tag.objects.create(
                name="Invalid",
                tag_type=Tag.TagType.USER,
                owner=None,  # Invalid: user tags need owner
            )

    def test_system_tag_must_not_have_owner(self, user):
        """System tags with owner should violate constraint."""
        with pytest.raises(IntegrityError):
            Tag.objects.create(
                name="Invalid",
                tag_type=Tag.TagType.SYSTEM,
                owner=user,  # Invalid: system tags can't have owner
            )

    def test_auto_tag_must_not_have_owner(self, user):
        """Auto tags with owner should violate constraint."""
        with pytest.raises(IntegrityError):
            Tag.objects.create(
                name="Invalid",
                tag_type=Tag.TagType.AUTO,
                owner=user,  # Invalid: auto tags can't have owner
            )


# =============================================================================
# MediaFileTag Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileTagCreation:
    """Tests for MediaFileTag associations."""

    def test_apply_tag_to_file(self, media_file, user):
        """Tag can be applied to a file."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        association, created = MediaFileTag.apply_tag(
            media_file=media_file,
            tag=tag,
            applied_by=user,
        )

        assert created is True
        assert association.media_file == media_file
        assert association.tag == tag
        assert association.applied_by == user
        assert association.confidence is None

    def test_apply_auto_tag_with_confidence(self, media_file):
        """Auto tags can have confidence scores."""
        tag = Tag.objects.create(
            name="Beach",
            tag_type=Tag.TagType.AUTO,
        )

        association, _ = MediaFileTag.apply_tag(
            media_file=media_file,
            tag=tag,
            confidence=0.95,
        )

        assert association.confidence == 0.95
        assert association.applied_by is None

    def test_apply_tag_idempotent(self, media_file, user):
        """Applying same tag twice returns existing association."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        assoc1, created1 = MediaFileTag.apply_tag(
            media_file=media_file,
            tag=tag,
            applied_by=user,
        )
        assoc2, created2 = MediaFileTag.apply_tag(
            media_file=media_file,
            tag=tag,
            applied_by=user,
        )

        assert created1 is True
        assert created2 is False
        assert assoc1.pk == assoc2.pk

    def test_remove_tag_from_file(self, media_file, user):
        """Tags can be removed from files."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )
        MediaFileTag.apply_tag(media_file=media_file, tag=tag)

        deleted = MediaFileTag.remove_tag(media_file=media_file, tag=tag)

        assert deleted == 1
        assert not MediaFileTag.objects.filter(media_file=media_file, tag=tag).exists()

    def test_remove_nonexistent_tag(self, media_file, user):
        """Removing non-applied tag returns 0."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        deleted = MediaFileTag.remove_tag(media_file=media_file, tag=tag)

        assert deleted == 0


@pytest.mark.django_db
class TestMediaFileTagConstraints:
    """Tests for MediaFileTag constraints."""

    def test_unique_tag_per_file(self, media_file, user):
        """Same tag cannot be applied twice to same file."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )
        MediaFileTag.objects.create(
            media_file=media_file,
            tag=tag,
            applied_by=user,
        )

        with pytest.raises(IntegrityError):
            MediaFileTag.objects.create(
                media_file=media_file,
                tag=tag,
                applied_by=user,
            )

    def test_confidence_range_valid(self, media_file):
        """Valid confidence values should be accepted."""
        tag = Tag.objects.create(
            name="Test",
            tag_type=Tag.TagType.AUTO,
        )

        # Test boundary values
        for confidence in [0.0, 0.5, 1.0]:
            assoc = MediaFileTag.objects.create(
                media_file=media_file,
                tag=tag,
                confidence=confidence,
            )
            assoc.delete()

    def test_confidence_range_invalid(self, media_file):
        """Invalid confidence values should be rejected."""
        tag = Tag.objects.create(
            name="Test",
            tag_type=Tag.TagType.AUTO,
        )

        with pytest.raises(IntegrityError):
            MediaFileTag.objects.create(
                media_file=media_file,
                tag=tag,
                confidence=1.5,  # Invalid: > 1.0
            )


# =============================================================================
# MediaFile Tag Methods Tests
# =============================================================================


@pytest.mark.django_db
class TestMediaFileTagMethods:
    """Tests for tag convenience methods on MediaFile."""

    def test_add_tag(self, media_file, user):
        """MediaFile.add_tag() should apply a tag."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        association, created = media_file.add_tag(tag, applied_by=user)

        assert created is True
        assert media_file.has_tag(tag) is True

    def test_remove_tag(self, media_file, user):
        """MediaFile.remove_tag() should remove a tag."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )
        media_file.add_tag(tag, applied_by=user)

        removed = media_file.remove_tag(tag)

        assert removed == 1
        assert media_file.has_tag(tag) is False

    def test_has_tag(self, media_file, user):
        """MediaFile.has_tag() should check tag presence."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )

        assert media_file.has_tag(tag) is False

        media_file.add_tag(tag, applied_by=user)

        assert media_file.has_tag(tag) is True

    def test_tags_property(self, media_file, user):
        """MediaFile.tags property should return all tags."""
        tag1 = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )
        tag2 = Tag.objects.create(
            name="Important",
            tag_type=Tag.TagType.USER,
            owner=user,
        )
        media_file.add_tag(tag1, applied_by=user)
        media_file.add_tag(tag2, applied_by=user)

        tags = list(media_file.tags)

        assert len(tags) == 2
        assert tag1 in tags
        assert tag2 in tags


# =============================================================================
# Tag Cascade Behavior Tests
# =============================================================================


@pytest.mark.django_db
class TestTagCascadeBehavior:
    """Tests for cascade delete behavior."""

    def test_tag_deletion_cascades_to_associations(self, media_file, user):
        """Deleting a tag should delete its file associations."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )
        media_file.add_tag(tag, applied_by=user)

        tag.delete()

        assert not MediaFileTag.objects.filter(tag_id=tag.pk).exists()

    def test_user_deletion_cascades_to_user_tags(self, user, media_file):
        """Deleting a user should delete their tags."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )
        tag_pk = tag.pk

        user.delete()

        assert not Tag.objects.filter(pk=tag_pk).exists()

    def test_media_file_soft_delete_preserves_tags(self, media_file, user):
        """Soft deleting a file should preserve tag associations."""
        tag = Tag.objects.create(
            name="Work",
            tag_type=Tag.TagType.USER,
            owner=user,
        )
        media_file.add_tag(tag, applied_by=user)
        association_pk = MediaFileTag.objects.get(media_file=media_file, tag=tag).pk

        media_file.soft_delete()

        # Association still exists
        assert MediaFileTag.objects.filter(pk=association_pk).exists()

    def test_applied_by_user_deletion_nullifies(self, media_file, user, other_user):
        """Deleting the user who applied a tag should nullify applied_by."""
        tag = Tag.objects.create(
            name="Featured",
            tag_type=Tag.TagType.SYSTEM,
        )
        media_file.add_tag(tag, applied_by=other_user)
        association = MediaFileTag.objects.get(media_file=media_file, tag=tag)
        assert association.applied_by == other_user

        other_user.delete()

        association.refresh_from_db()
        assert association.applied_by is None
