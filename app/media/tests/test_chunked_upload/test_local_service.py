"""
Tests for LocalChunkedUploadService.

Following TDD: These tests define the expected behavior before implementation.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.utils import timezone

from media.models import MediaFile, UploadSession
from media.services.chunked_upload.local import LocalChunkedUploadService

if TYPE_CHECKING:
    from authentication.models import User


pytestmark = pytest.mark.django_db


class TestLocalSessionCreation:
    """Tests for LocalChunkedUploadService.create_session()."""

    def test_create_session_success(
        self,
        upload_user: "User",
        tmp_path: Path,
    ) -> None:
        """Session created with correct metadata."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.create_session(
            user=upload_user,
            filename="test_video.mp4",
            file_size=15 * 1024 * 1024,  # 15MB
            mime_type="video/mp4",
            media_type="video",
        )

        assert result.success
        session = result.data
        assert session is not None
        assert session.filename == "test_video.mp4"
        assert session.file_size == 15 * 1024 * 1024
        assert session.mime_type == "video/mp4"
        assert session.media_type == "video"
        assert session.uploader == upload_user
        assert session.backend == UploadSession.Backend.LOCAL
        assert session.status == UploadSession.Status.IN_PROGRESS
        assert session.local_temp_dir is not None
        assert session.chunk_size == 5 * 1024 * 1024  # Default 5MB
        assert session.bytes_received == 0
        assert session.parts_completed == []

    def test_create_session_creates_temp_directory(
        self,
        upload_user: "User",
        tmp_path: Path,
    ) -> None:
        """Session creation creates the temporary directory."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.create_session(
            user=upload_user,
            filename="test.mp4",
            file_size=10 * 1024 * 1024,
            mime_type="video/mp4",
            media_type="video",
        )

        assert result.success
        session = result.data
        assert os.path.isdir(session.local_temp_dir)

    def test_create_session_checks_quota_before_creating(
        self,
        user_at_quota: "User",
        tmp_path: Path,
    ) -> None:
        """Quota exceeded returns failure, no session created."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.create_session(
            user=user_at_quota,
            filename="too_big.mp4",
            file_size=50 * 1024 * 1024,  # 50MB, but quota is exhausted
            mime_type="video/mp4",
            media_type="video",
        )

        assert not result.success
        assert "quota" in result.error.lower()
        assert UploadSession.objects.filter(uploader=user_at_quota).count() == 0

    def test_create_session_calculates_correct_parts(
        self,
        upload_user: "User",
        tmp_path: Path,
    ) -> None:
        """total_parts calculated correctly from file_size / chunk_size."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # 15MB file with 5MB chunks = 3 parts
        result = service.create_session(
            user=upload_user,
            filename="test.mp4",
            file_size=15 * 1024 * 1024,
            mime_type="video/mp4",
            media_type="video",
        )

        assert result.success
        assert result.data.total_parts == 3

        # 11MB file with 5MB chunks = 3 parts (ceiling)
        result2 = service.create_session(
            user=upload_user,
            filename="test2.mp4",
            file_size=11 * 1024 * 1024,
            mime_type="video/mp4",
            media_type="video",
        )

        assert result2.success
        assert result2.data.total_parts == 3

    def test_create_session_sets_expiration(
        self,
        upload_user: "User",
        tmp_path: Path,
    ) -> None:
        """expires_at set to configured hours from now."""
        service = LocalChunkedUploadService(
            temp_base_dir=str(tmp_path / "chunks"),
            expiry_hours=24,
        )

        before = timezone.now()
        result = service.create_session(
            user=upload_user,
            filename="test.mp4",
            file_size=10 * 1024 * 1024,
            mime_type="video/mp4",
            media_type="video",
        )
        after = timezone.now()

        assert result.success
        session = result.data
        # Expiration should be approximately 24 hours from now
        expected_min = before + timedelta(hours=24)
        expected_max = after + timedelta(hours=24)
        assert expected_min <= session.expires_at <= expected_max


class TestLocalChunkTarget:
    """Tests for LocalChunkedUploadService.get_chunk_target()."""

    def test_get_target_returns_server_endpoint(
        self,
        local_session: UploadSession,
        tmp_path: Path,
    ) -> None:
        """Local backend returns endpoint URL pointing to our server."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.get_chunk_target(local_session, part_number=1)

        assert result.success
        target = result.data
        assert str(local_session.id) in target.upload_url
        assert "1" in target.upload_url  # part number in URL
        assert target.direct is False
        assert target.method == "PUT"

    def test_get_target_expired_session(
        self,
        expired_session: UploadSession,
        tmp_path: Path,
    ) -> None:
        """Expired session returns error."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.get_chunk_target(expired_session, part_number=1)

        assert not result.success
        assert "expired" in result.error.lower()

    def test_get_target_invalid_part_number(
        self,
        local_session: UploadSession,
        tmp_path: Path,
    ) -> None:
        """Part number out of range returns error."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # Part 0 (invalid - 1-indexed)
        result = service.get_chunk_target(local_session, part_number=0)
        assert not result.success

        # Part > total_parts (local_session has 3 parts)
        result = service.get_chunk_target(local_session, part_number=10)
        assert not result.success


class TestLocalReceiveChunk:
    """Tests for LocalChunkedUploadService.receive_chunk()."""

    def test_receive_chunk_writes_to_temp_directory(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """Chunk data is written to temp directory as part file."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.receive_chunk(
            session=local_session,
            part_number=1,
            data=chunk_data_5mb,
        )

        assert result.success
        # Check file was written
        part_path = Path(local_session.local_temp_dir) / "part_0001"
        assert part_path.exists()
        assert part_path.read_bytes() == chunk_data_5mb

    def test_receive_chunk_updates_progress(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """bytes_received and parts_completed updated after chunk."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.receive_chunk(
            session=local_session,
            part_number=1,
            data=chunk_data_5mb,
        )

        assert result.success
        assert result.data.bytes_received == len(chunk_data_5mb)
        assert result.data.parts_completed == 1

        # Refresh from DB
        local_session.refresh_from_db()
        assert local_session.bytes_received == len(chunk_data_5mb)
        assert len(local_session.parts_completed) == 1

    def test_receive_chunk_handles_duplicates_idempotently(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """Uploading same part twice is idempotent."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # Upload part 1 twice
        result1 = service.receive_chunk(local_session, 1, chunk_data_5mb)
        result2 = service.receive_chunk(local_session, 1, chunk_data_5mb)

        assert result1.success
        assert result2.success
        # bytes_received should not double-count
        assert result2.data.bytes_received == len(chunk_data_5mb)
        assert result2.data.parts_completed == 1

    def test_receive_chunk_validates_session_status(
        self,
        completed_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """Cannot upload to completed session."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.receive_chunk(
            session=completed_session,
            part_number=1,
            data=chunk_data_5mb,
        )

        assert not result.success
        assert "status" in result.error.lower() or "completed" in result.error.lower()

    def test_is_complete_when_all_parts_received(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        chunk_data_1mb: bytes,
        tmp_path: Path,
    ) -> None:
        """is_complete=True when bytes_received >= file_size."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # Session has 15MB file, need 3 x 5MB parts
        # Adjust to realistic scenario: 2 x 5MB + 1 x 5MB = 15MB
        result1 = service.receive_chunk(local_session, 1, chunk_data_5mb)
        assert result1.data.is_complete is False

        result2 = service.receive_chunk(local_session, 2, chunk_data_5mb)
        assert result2.data.is_complete is False

        result3 = service.receive_chunk(local_session, 3, chunk_data_5mb)
        assert result3.data.is_complete is True


class TestLocalFinalization:
    """Tests for LocalChunkedUploadService.finalize_upload()."""

    def test_finalize_requires_all_parts(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """Missing parts returns error with list of missing parts."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # Upload only part 1 of 3
        service.receive_chunk(local_session, 1, chunk_data_5mb)

        result = service.finalize_upload(local_session)

        assert not result.success
        assert "missing" in result.error.lower() or "incomplete" in result.error.lower()

    def test_finalize_creates_media_file(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """MediaFile created with correct metadata after finalization."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # Upload all 3 parts
        service.receive_chunk(local_session, 1, chunk_data_5mb)
        service.receive_chunk(local_session, 2, chunk_data_5mb)
        service.receive_chunk(local_session, 3, chunk_data_5mb)

        result = service.finalize_upload(local_session)

        assert result.success
        media_file = result.data
        assert isinstance(media_file, MediaFile)
        assert media_file.original_filename == local_session.filename
        assert media_file.mime_type == local_session.mime_type
        assert media_file.media_type == local_session.media_type
        assert media_file.uploader == local_session.uploader
        assert media_file.file_size == local_session.file_size

    def test_finalize_updates_profile_quota(
        self,
        upload_user: "User",
        tmp_path: Path,
        chunk_data_5mb: bytes,
    ) -> None:
        """profile.add_storage_usage called with file size."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        initial_storage = upload_user.profile.total_storage_bytes

        # Create session
        result = service.create_session(
            user=upload_user,
            filename="test.mp4",
            file_size=15 * 1024 * 1024,
            mime_type="video/mp4",
            media_type="video",
        )
        session = result.data

        # Upload all parts
        service.receive_chunk(session, 1, chunk_data_5mb)
        service.receive_chunk(session, 2, chunk_data_5mb)
        service.receive_chunk(session, 3, chunk_data_5mb)

        # Finalize
        service.finalize_upload(session)

        # Check quota updated
        upload_user.profile.refresh_from_db()
        expected_storage = initial_storage + (15 * 1024 * 1024)
        assert upload_user.profile.total_storage_bytes == expected_storage

    def test_finalize_triggers_processing_pipeline(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """scan_file_for_malware task chain is triggered."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # Upload all parts
        service.receive_chunk(local_session, 1, chunk_data_5mb)
        service.receive_chunk(local_session, 2, chunk_data_5mb)
        service.receive_chunk(local_session, 3, chunk_data_5mb)

        with patch("media.services.chunked_upload.local.chain") as mock_chain:
            result = service.finalize_upload(local_session)

            assert result.success
            # Chain should be called to trigger pipeline
            mock_chain.assert_called_once()

    def test_finalize_version_group_self_reference(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """version_group points to self for new files."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # Upload all parts
        service.receive_chunk(local_session, 1, chunk_data_5mb)
        service.receive_chunk(local_session, 2, chunk_data_5mb)
        service.receive_chunk(local_session, 3, chunk_data_5mb)

        result = service.finalize_upload(local_session)

        assert result.success
        media_file = result.data
        assert media_file.version == 1
        assert media_file.is_current is True
        assert media_file.version_group_id == media_file.id

    def test_finalize_marks_session_completed(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """Session status changes to COMPLETED."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # Upload all parts
        service.receive_chunk(local_session, 1, chunk_data_5mb)
        service.receive_chunk(local_session, 2, chunk_data_5mb)
        service.receive_chunk(local_session, 3, chunk_data_5mb)

        result = service.finalize_upload(local_session)

        assert result.success
        local_session.refresh_from_db()
        assert local_session.status == UploadSession.Status.COMPLETED

    def test_finalize_cleans_up_temp_directory(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """Local temp directory deleted after successful finalization."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))
        temp_dir = local_session.local_temp_dir

        # Upload all parts
        service.receive_chunk(local_session, 1, chunk_data_5mb)
        service.receive_chunk(local_session, 2, chunk_data_5mb)
        service.receive_chunk(local_session, 3, chunk_data_5mb)

        # Temp dir exists before finalization
        assert os.path.isdir(temp_dir)

        result = service.finalize_upload(local_session)

        assert result.success
        # Temp dir should be deleted
        assert not os.path.exists(temp_dir)

    def test_finalize_assembles_file_correctly(
        self,
        upload_user: "User",
        tmp_path: Path,
    ) -> None:
        """Chunks concatenated in correct order."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        # Create distinct chunk data
        chunk1 = b"CHUNK1" * 100
        chunk2 = b"CHUNK2" * 100
        chunk3 = b"CHUNK3" * 100
        total_size = len(chunk1) + len(chunk2) + len(chunk3)

        # Create session with custom size
        result = service.create_session(
            user=upload_user,
            filename="test.bin",
            file_size=total_size,
            mime_type="application/octet-stream",
            media_type="other",
        )
        session = result.data
        # Adjust chunk size for test
        session.chunk_size = len(chunk1)
        session.save()

        # Upload chunks out of order
        service.receive_chunk(session, 2, chunk2)
        service.receive_chunk(session, 1, chunk1)
        service.receive_chunk(session, 3, chunk3)

        result = service.finalize_upload(session)

        assert result.success
        media_file = result.data

        # Verify assembled file content
        with media_file.file.open("rb") as f:
            content = f.read()
            assert content == chunk1 + chunk2 + chunk3


class TestLocalAbort:
    """Tests for LocalChunkedUploadService.abort_upload()."""

    def test_abort_marks_session_failed(
        self,
        local_session: UploadSession,
        tmp_path: Path,
    ) -> None:
        """Session status changes to FAILED."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.abort_upload(local_session)

        assert result.success
        local_session.refresh_from_db()
        assert local_session.status == UploadSession.Status.FAILED

    def test_abort_deletes_temp_directory(
        self,
        local_session: UploadSession,
        chunk_data_5mb: bytes,
        tmp_path: Path,
    ) -> None:
        """Temp directory removed on abort."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))
        temp_dir = local_session.local_temp_dir

        # Upload a chunk
        service.receive_chunk(local_session, 1, chunk_data_5mb)
        assert os.path.isdir(temp_dir)

        result = service.abort_upload(local_session)

        assert result.success
        assert not os.path.exists(temp_dir)

    def test_abort_completed_session_fails(
        self,
        completed_session: UploadSession,
        tmp_path: Path,
    ) -> None:
        """Cannot abort completed session."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        result = service.abort_upload(completed_session)

        assert not result.success
        assert "completed" in result.error.lower() or "cannot" in result.error.lower()


class TestLocalSessionProgress:
    """Tests for LocalChunkedUploadService.get_session_progress()."""

    def test_get_progress_returns_correct_info(
        self,
        session_with_parts: UploadSession,
        tmp_path: Path,
    ) -> None:
        """Progress info includes all expected fields."""
        service = LocalChunkedUploadService(temp_base_dir=str(tmp_path / "chunks"))

        progress = service.get_session_progress(session_with_parts)

        assert progress["session_id"] == str(session_with_parts.id)
        assert progress["filename"] == session_with_parts.filename
        assert progress["file_size"] == session_with_parts.file_size
        assert progress["bytes_received"] == session_with_parts.bytes_received
        assert progress["parts_completed"] == 2
        assert progress["total_parts"] == 3
        assert 0 <= progress["progress_percent"] <= 100
        assert progress["status"] == session_with_parts.status
