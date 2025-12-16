"""
Tests for the quarantine service.

These tests verify:
- File movement to quarantine directory
- Metadata file creation
- MediaFile record updates
- Storage quota reclamation
- Restoration from quarantine
- Listing and cleanup functions
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from django.conf import settings

from authentication.tests.factories import UserFactory
from media.models import MediaFile
from media.services.quarantine import (
    _get_quarantine_dir,
    _get_quarantine_file_dir,
    cleanup_old_quarantine,
    list_quarantined_files,
    quarantine_infected_file,
    restore_from_quarantine,
)


@pytest.fixture
def user_with_storage(db):
    """Create a user with storage quota."""
    user = UserFactory(email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB
    user.profile.total_storage_bytes = 100 * 1024  # 100KB used
    user.profile.save()
    return user


@pytest.fixture
def temp_upload_file():
    """Create a temporary file simulating an uploaded file."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"This is test content for quarantine testing.")
        return Path(f.name)


@pytest.fixture
def media_file_for_quarantine(db, user_with_storage, temp_upload_file):
    """Create a MediaFile instance with an actual file for quarantine testing."""
    # Create a mock file path within MEDIA_ROOT
    media_root = Path(settings.MEDIA_ROOT)
    test_dir = media_root / "test_uploads"
    test_dir.mkdir(parents=True, exist_ok=True)

    file_path = test_dir / "test_file.txt"
    file_content = b"This is test content for quarantine testing."
    file_path.write_bytes(file_content)

    # Create MediaFile with the file
    media_file = MediaFile.objects.create(
        file="test_uploads/test_file.txt",
        original_filename="test_file.txt",
        media_type=MediaFile.MediaType.DOCUMENT,
        mime_type="text/plain",
        file_size=len(file_content),
        uploader=user_with_storage,
        visibility=MediaFile.Visibility.PRIVATE,
        scan_status=MediaFile.ScanStatus.PENDING,
    )

    yield media_file

    # Cleanup
    if file_path.exists():
        file_path.unlink()
    if test_dir.exists():
        import shutil

        shutil.rmtree(test_dir, ignore_errors=True)


@pytest.fixture
def cleanup_quarantine():
    """Cleanup quarantine directory after test."""
    yield
    import shutil

    quarantine_dir = _get_quarantine_dir()
    if quarantine_dir.exists():
        shutil.rmtree(quarantine_dir, ignore_errors=True)


class TestQuarantineDirectoryHelpers:
    """Test quarantine directory helper functions."""

    def test_get_quarantine_dir(self):
        """Should return correct quarantine path."""
        quarantine_dir = _get_quarantine_dir()

        assert quarantine_dir.parent == Path(settings.MEDIA_ROOT)
        assert quarantine_dir.name == settings.CLAMAV_QUARANTINE_DIR

    def test_get_quarantine_file_dir(self):
        """Should return date-partitioned quarantine path."""
        from datetime import date

        file_id = "test-uuid-123"
        quarantine_file_dir = _get_quarantine_file_dir(file_id)

        today = date.today()
        assert today.isoformat() in str(quarantine_file_dir)
        assert file_id in str(quarantine_file_dir)


class TestQuarantineInfectedFile:
    """Test the quarantine_infected_file function."""

    def test_successful_quarantine(
        self, media_file_for_quarantine: MediaFile, cleanup_quarantine
    ):
        """Should move file to quarantine and update record."""
        result = quarantine_infected_file(
            media_file_for_quarantine,
            threat_name="Eicar-Signature",
        )

        assert result.success is True

        # Verify file was moved
        original_path = Path(settings.MEDIA_ROOT) / "test_uploads" / "test_file.txt"
        assert not original_path.exists()

        # Verify quarantine file exists
        quarantine_path = Path(result.data)
        assert quarantine_path.exists()

        # Verify metadata file exists
        metadata_path = quarantine_path.parent / "metadata.json"
        assert metadata_path.exists()

        with open(metadata_path) as f:
            metadata = json.load(f)
            assert metadata["threat_name"] == "Eicar-Signature"
            assert metadata["media_file_id"] == str(media_file_for_quarantine.id)

    def test_updates_media_file_record(
        self, media_file_for_quarantine: MediaFile, cleanup_quarantine
    ):
        """Should update MediaFile scan_status and threat_name."""
        quarantine_infected_file(
            media_file_for_quarantine,
            threat_name="TestVirus.A",
        )

        media_file_for_quarantine.refresh_from_db()

        assert media_file_for_quarantine.scan_status == MediaFile.ScanStatus.INFECTED
        assert media_file_for_quarantine.threat_name == "TestVirus.A"
        assert media_file_for_quarantine.scanned_at is not None
        assert media_file_for_quarantine.file == ""

    def test_reclaims_storage_quota(
        self, media_file_for_quarantine: MediaFile, cleanup_quarantine
    ):
        """Should subtract file size from user's storage usage."""
        initial_storage = media_file_for_quarantine.uploader.profile.total_storage_bytes
        file_size = media_file_for_quarantine.file_size

        quarantine_infected_file(
            media_file_for_quarantine,
            threat_name="TestVirus.B",
        )

        media_file_for_quarantine.uploader.profile.refresh_from_db()
        expected_storage = max(0, initial_storage - file_size)
        assert (
            media_file_for_quarantine.uploader.profile.total_storage_bytes
            == expected_storage
        )

    def test_file_not_found_error(self, db, user_with_storage, cleanup_quarantine):
        """Should return error if file doesn't exist."""
        # Create MediaFile without actual file
        media_file = MediaFile.objects.create(
            file="nonexistent/path.txt",
            original_filename="missing.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=100,
            uploader=user_with_storage,
        )

        result = quarantine_infected_file(media_file, "TestVirus.C")

        assert result.success is False
        assert result.error_code == "file_not_found"


class TestRestoreFromQuarantine:
    """Test the restore_from_quarantine function."""

    def test_successful_restore(
        self, media_file_for_quarantine: MediaFile, cleanup_quarantine
    ):
        """Should restore file from quarantine."""
        # First quarantine the file
        quarantine_result = quarantine_infected_file(
            media_file_for_quarantine,
            threat_name="TestVirus.D",
        )
        assert quarantine_result.success is True

        # Now restore it
        restore_result = restore_from_quarantine(str(media_file_for_quarantine.id))

        assert restore_result.success is True

        # Verify file was restored
        restored_path = Path(restore_result.data)
        assert restored_path.exists()

        # Verify MediaFile record updated
        media_file_for_quarantine.refresh_from_db()
        assert media_file_for_quarantine.scan_status == MediaFile.ScanStatus.CLEAN
        assert media_file_for_quarantine.threat_name is None

    def test_restore_adds_storage_quota(
        self, media_file_for_quarantine: MediaFile, cleanup_quarantine
    ):
        """Should add file size back to user's storage usage."""
        file_size = media_file_for_quarantine.file_size

        # Quarantine
        quarantine_infected_file(media_file_for_quarantine, "TestVirus.E")

        storage_after_quarantine = (
            media_file_for_quarantine.uploader.profile.total_storage_bytes
        )

        # Restore
        restore_from_quarantine(str(media_file_for_quarantine.id))

        media_file_for_quarantine.uploader.profile.refresh_from_db()
        assert (
            media_file_for_quarantine.uploader.profile.total_storage_bytes
            == storage_after_quarantine + file_size
        )

    def test_restore_nonexistent_media_file(self, db, cleanup_quarantine):
        """Should return error for nonexistent MediaFile."""
        result = restore_from_quarantine("nonexistent-uuid")

        assert result.success is False
        assert result.error_code == "media_file_not_found"

    def test_restore_non_quarantined_file(
        self, db, user_with_storage, cleanup_quarantine
    ):
        """Should return error if file is not quarantined."""
        media_file = MediaFile.objects.create(
            file="some/path.txt",
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=100,
            uploader=user_with_storage,
            scan_status=MediaFile.ScanStatus.CLEAN,
        )

        result = restore_from_quarantine(str(media_file.id))

        assert result.success is False
        assert result.error_code == "not_quarantined"


class TestListQuarantinedFiles:
    """Test the list_quarantined_files function."""

    def test_list_empty_quarantine(self, cleanup_quarantine):
        """Should return empty list when no files quarantined."""
        result = list_quarantined_files()
        assert result == []

    def test_list_quarantined_files(
        self, media_file_for_quarantine: MediaFile, cleanup_quarantine
    ):
        """Should list quarantined files with metadata."""
        quarantine_infected_file(media_file_for_quarantine, "TestVirus.F")

        result = list_quarantined_files()

        assert len(result) == 1
        assert result[0]["threat_name"] == "TestVirus.F"
        assert result[0]["media_file_id"] == str(media_file_for_quarantine.id)


class TestCleanupOldQuarantine:
    """Test the cleanup_old_quarantine function."""

    def test_cleanup_removes_old_entries(self, cleanup_quarantine):
        """Should remove quarantine entries older than specified days."""
        from datetime import date, timedelta

        quarantine_base = _get_quarantine_dir()

        # Create old quarantine directory (100 days ago)
        old_date = date.today() - timedelta(days=100)
        old_dir = quarantine_base / old_date.isoformat() / "old-file-id"
        old_dir.mkdir(parents=True)
        (old_dir / "metadata.json").write_text("{}")

        # Create recent quarantine directory (1 day ago)
        recent_date = date.today() - timedelta(days=1)
        recent_dir = quarantine_base / recent_date.isoformat() / "recent-file-id"
        recent_dir.mkdir(parents=True)
        (recent_dir / "metadata.json").write_text("{}")

        # Cleanup entries older than 90 days
        removed = cleanup_old_quarantine(days=90)

        assert removed == 1
        assert not (quarantine_base / old_date.isoformat()).exists()
        assert (quarantine_base / recent_date.isoformat()).exists()

    def test_cleanup_empty_quarantine(self, cleanup_quarantine):
        """Should return 0 when quarantine is empty."""
        removed = cleanup_old_quarantine(days=90)
        assert removed == 0
