"""
Tests for MediaAsset model.

Tests cover:
- Model creation and validation
- Asset path generation
- Unique constraint enforcement
- Cascade delete behavior
- Dimension property
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from PIL import Image

from media.models import MediaAsset, MediaFile


@pytest.fixture
def media_file(user, sample_jpeg_uploaded):
    """Create a MediaFile instance for testing."""
    return MediaFile.create_from_upload(
        file=sample_jpeg_uploaded,
        uploader=user,
        media_type="image",
        mime_type="image/jpeg",
    )


@pytest.fixture
def thumbnail_file():
    """Generate a small WebP thumbnail file."""
    image = Image.new("RGB", (50, 50), color="blue")
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP")
    buffer.seek(0)
    return SimpleUploadedFile(
        name="thumb.webp",
        content=buffer.read(),
        content_type="image/webp",
    )


@pytest.mark.django_db
class TestMediaAssetModel:
    """Tests for MediaAsset model creation and basic operations."""

    def test_create_thumbnail_asset(self, media_file, thumbnail_file):
        """Test creating a thumbnail asset for a media file."""
        asset = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            width=50,
            height=50,
            file_size=len(thumbnail_file.read()),
        )

        assert asset.id is not None
        assert asset.media_file == media_file
        assert asset.asset_type == MediaAsset.AssetType.THUMBNAIL
        assert asset.width == 50
        assert asset.height == 50
        assert asset.file_size > 0

    def test_asset_str_representation(self, media_file, thumbnail_file):
        """Test string representation of MediaAsset."""
        asset = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            width=50,
            height=50,
            file_size=100,
        )

        assert str(asset) == f"thumbnail for {media_file.id}"

    def test_asset_dimensions_property(self, media_file, thumbnail_file):
        """Test dimensions property returns formatted string."""
        asset = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            width=200,
            height=150,
            file_size=100,
        )

        assert asset.dimensions == "200x150"

    def test_asset_dimensions_none_when_not_set(self, media_file, thumbnail_file):
        """Test dimensions property returns None when not set."""
        asset = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            file_size=100,
        )

        assert asset.dimensions is None

    def test_asset_types_enum(self):
        """Test all expected asset types are defined."""
        asset_types = [choice[0] for choice in MediaAsset.AssetType.choices]

        assert "thumbnail" in asset_types
        assert "preview" in asset_types
        assert "web_optimized" in asset_types
        assert "poster" in asset_types
        assert "transcoded" in asset_types


@pytest.mark.django_db
class TestMediaAssetPathGeneration:
    """Tests for asset upload path generation."""

    def test_path_mirrors_parent_structure(self, media_file, thumbnail_file):
        """Test that asset path mirrors parent MediaFile path structure."""
        asset = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            width=50,
            height=50,
            file_size=100,
        )

        # Path should start with 'assets/' and include asset_type
        assert asset.file.name.startswith("assets/")
        assert "thumbnail" in asset.file.name

    def test_path_contains_media_type(self, media_file, thumbnail_file):
        """Test that asset path contains the media type."""
        asset = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            file_size=100,
        )

        # Path should contain the media type from parent
        assert "image" in asset.file.name or media_file.media_type in asset.file.name


@pytest.mark.django_db
class TestMediaAssetConstraints:
    """Tests for MediaAsset database constraints."""

    def test_unique_asset_type_per_media_file(self, media_file, thumbnail_file):
        """Test that only one asset of each type is allowed per media file."""
        # Create first thumbnail
        MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            file_size=100,
        )

        # Attempt to create second thumbnail for same file
        # Create a new thumbnail file to avoid Django's file handling issues
        image = Image.new("RGB", (50, 50), color="green")
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP")
        buffer.seek(0)
        second_thumbnail = SimpleUploadedFile(
            name="thumb2.webp",
            content=buffer.read(),
            content_type="image/webp",
        )

        with pytest.raises(IntegrityError):
            MediaAsset.objects.create(
                media_file=media_file,
                asset_type=MediaAsset.AssetType.THUMBNAIL,
                file=second_thumbnail,
                file_size=100,
            )

    def test_different_asset_types_allowed(self, media_file):
        """Test that different asset types can coexist for same media file."""
        # Create thumbnail
        image1 = Image.new("RGB", (50, 50), color="red")
        buffer1 = io.BytesIO()
        image1.save(buffer1, format="WEBP")
        buffer1.seek(0)
        thumb_file = SimpleUploadedFile(
            name="thumb.webp",
            content=buffer1.read(),
            content_type="image/webp",
        )

        thumbnail = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumb_file,
            file_size=100,
        )

        # Create preview
        image2 = Image.new("RGB", (200, 200), color="blue")
        buffer2 = io.BytesIO()
        image2.save(buffer2, format="WEBP")
        buffer2.seek(0)
        preview_file = SimpleUploadedFile(
            name="preview.webp",
            content=buffer2.read(),
            content_type="image/webp",
        )

        preview = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.PREVIEW,
            file=preview_file,
            file_size=200,
        )

        assert thumbnail.id != preview.id
        assert media_file.assets.count() == 2


@pytest.mark.django_db
class TestMediaAssetCascadeDelete:
    """Tests for cascade delete behavior."""

    def test_deleting_media_file_deletes_assets(self, media_file, thumbnail_file):
        """Test that deleting a MediaFile deletes all its assets."""
        asset = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            file_size=100,
        )
        asset_id = asset.id

        # Delete the parent MediaFile
        media_file.delete()

        # Asset should be deleted
        assert not MediaAsset.objects.filter(id=asset_id).exists()

    def test_soft_delete_media_file_keeps_assets(self, media_file, thumbnail_file):
        """Test that soft-deleting MediaFile keeps assets (for potential recovery)."""
        asset = MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            file_size=100,
        )
        asset_id = asset.id
        media_file_id = media_file.id

        # Soft delete the parent MediaFile
        media_file.is_deleted = True
        media_file.save()

        # Asset should still exist (soft delete doesn't cascade)
        assert MediaAsset.objects.filter(id=asset_id).exists()

        # MediaFile should still be in DB (soft delete marks, doesn't remove)
        assert MediaFile.all_objects.filter(id=media_file_id).exists()
        # Verify it's marked as deleted
        media_file.refresh_from_db()
        assert media_file.is_deleted is True


@pytest.mark.django_db
class TestMediaAssetRelatedName:
    """Tests for the 'assets' related name on MediaFile."""

    def test_access_assets_via_related_name(self, media_file, thumbnail_file):
        """Test accessing assets through MediaFile.assets."""
        MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.THUMBNAIL,
            file=thumbnail_file,
            file_size=100,
        )

        assert media_file.assets.count() == 1
        assert media_file.assets.first().asset_type == MediaAsset.AssetType.THUMBNAIL

    def test_filter_assets_by_type(self, media_file):
        """Test filtering assets by type using related manager."""
        # Create multiple assets
        for asset_type in [
            MediaAsset.AssetType.THUMBNAIL,
            MediaAsset.AssetType.PREVIEW,
        ]:
            image = Image.new("RGB", (50, 50), color="red")
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP")
            buffer.seek(0)
            file = SimpleUploadedFile(
                name=f"{asset_type}.webp",
                content=buffer.read(),
                content_type="image/webp",
            )
            MediaAsset.objects.create(
                media_file=media_file,
                asset_type=asset_type,
                file=file,
                file_size=100,
            )

        # Filter by type
        thumbnails = media_file.assets.filter(asset_type=MediaAsset.AssetType.THUMBNAIL)
        assert thumbnails.count() == 1
