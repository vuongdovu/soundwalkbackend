"""
Test fixtures for chunked upload tests.

Provides fixtures for:
- Upload sessions in various states
- Chunk data for testing uploads
- Mock S3 clients
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Generator

import pytest
from django.utils import timezone

from authentication.tests.factories import UserFactory
from media.models import UploadSession

if TYPE_CHECKING:
    from authentication.models import User


# =============================================================================
# User Fixtures
# =============================================================================


@pytest.fixture
def upload_user(db) -> "User":
    """Create a user with sufficient storage quota for uploads."""
    user = UserFactory(email_verified=True)
    user.profile.storage_quota_bytes = 5 * 1024 * 1024 * 1024  # 5GB
    user.profile.total_storage_bytes = 0
    user.profile.save()
    return user


@pytest.fixture
def user_near_quota_chunked(db) -> "User":
    """Create a user with very limited remaining quota (1MB)."""
    user = UserFactory(email_verified=True)
    user.profile.storage_quota_bytes = 100 * 1024 * 1024  # 100MB
    user.profile.total_storage_bytes = 99 * 1024 * 1024  # 99MB used
    user.profile.save()
    return user


@pytest.fixture
def user_at_quota(db) -> "User":
    """Create a user who has exhausted their quota."""
    user = UserFactory(email_verified=True)
    user.profile.storage_quota_bytes = 100 * 1024 * 1024  # 100MB
    user.profile.total_storage_bytes = 100 * 1024 * 1024  # All used
    user.profile.save()
    return user


# =============================================================================
# Chunk Data Fixtures
# =============================================================================


@pytest.fixture
def chunk_data_5mb() -> bytes:
    """Generate 5MB of random-ish data for a chunk."""
    # Use a repeating pattern for efficiency
    return b"x" * (5 * 1024 * 1024)


@pytest.fixture
def chunk_data_1mb() -> bytes:
    """Generate 1MB of data (for last chunk scenarios)."""
    return b"y" * (1 * 1024 * 1024)


@pytest.fixture
def small_file_data() -> bytes:
    """Generate a small file that fits in one chunk."""
    return b"small file content" * 1000  # ~18KB


# =============================================================================
# Session Fixtures
# =============================================================================


@pytest.fixture
def local_session(upload_user: "User", tmp_path: Path) -> UploadSession:
    """Create a local backend upload session."""
    session = UploadSession.objects.create(
        uploader=upload_user,
        filename="test_video.mp4",
        file_size=15 * 1024 * 1024,  # 15MB (3 chunks)
        mime_type="video/mp4",
        media_type="video",
        backend=UploadSession.Backend.LOCAL,
        local_temp_dir=str(tmp_path / "chunks" / str(uuid.uuid4())),
        chunk_size=5 * 1024 * 1024,  # 5MB
        expires_at=timezone.now() + timedelta(hours=24),
    )
    # Create the temp directory
    os.makedirs(session.local_temp_dir, exist_ok=True)
    return session


@pytest.fixture
def s3_session(upload_user: "User") -> UploadSession:
    """Create an S3 backend upload session."""
    return UploadSession.objects.create(
        uploader=upload_user,
        filename="test_video.mp4",
        file_size=15 * 1024 * 1024,  # 15MB (3 chunks)
        mime_type="video/mp4",
        media_type="video",
        backend=UploadSession.Backend.S3,
        s3_key=f"uploads/pending/{uuid.uuid4()}/test_video.mp4",
        s3_upload_id="mock-upload-id-12345",
        chunk_size=5 * 1024 * 1024,  # 5MB
        expires_at=timezone.now() + timedelta(hours=24),
    )


@pytest.fixture
def expired_session(upload_user: "User") -> UploadSession:
    """Create an expired upload session."""
    return UploadSession.objects.create(
        uploader=upload_user,
        filename="expired_file.mp4",
        file_size=10 * 1024 * 1024,
        mime_type="video/mp4",
        media_type="video",
        backend=UploadSession.Backend.LOCAL,
        local_temp_dir=f"/tmp/chunks/{uuid.uuid4()}",
        chunk_size=5 * 1024 * 1024,
        expires_at=timezone.now() - timedelta(hours=1),  # Expired
    )


@pytest.fixture
def completed_session(upload_user: "User") -> UploadSession:
    """Create a completed upload session."""
    return UploadSession.objects.create(
        uploader=upload_user,
        filename="completed_file.mp4",
        file_size=10 * 1024 * 1024,
        mime_type="video/mp4",
        media_type="video",
        backend=UploadSession.Backend.LOCAL,
        local_temp_dir=f"/tmp/chunks/{uuid.uuid4()}",
        chunk_size=5 * 1024 * 1024,
        status=UploadSession.Status.COMPLETED,
        expires_at=timezone.now() + timedelta(hours=24),
    )


@pytest.fixture
def session_with_parts(
    local_session: UploadSession, chunk_data_5mb: bytes
) -> UploadSession:
    """Create a session with some parts already uploaded."""
    # Write first two parts
    temp_dir = Path(local_session.local_temp_dir)

    part_1_path = temp_dir / "part_0001"
    part_1_path.write_bytes(chunk_data_5mb)

    part_2_path = temp_dir / "part_0002"
    part_2_path.write_bytes(chunk_data_5mb)

    # Update session progress
    local_session.parts_completed = [
        {"part_number": 1, "etag": "etag1", "size": len(chunk_data_5mb)},
        {"part_number": 2, "etag": "etag2", "size": len(chunk_data_5mb)},
    ]
    local_session.bytes_received = 2 * len(chunk_data_5mb)
    local_session.save()

    return local_session


# =============================================================================
# Temporary Directory Cleanup
# =============================================================================


@pytest.fixture
def chunks_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create and manage a temporary chunks directory."""
    chunks_path = tmp_path / "chunks"
    chunks_path.mkdir(exist_ok=True)
    yield chunks_path
    # Cleanup happens automatically via tmp_path


@pytest.fixture(autouse=True)
def clean_chunks_after_test(tmp_path: Path) -> Generator[None, None, None]:
    """Ensure chunk directories are cleaned up after each test."""
    yield
    # Cleanup is handled by pytest's tmp_path fixture


# =============================================================================
# Mock Settings
# =============================================================================


@pytest.fixture
def chunked_upload_settings(settings, tmp_path: Path) -> None:
    """Configure chunked upload settings for tests."""
    settings.CHUNKED_UPLOAD_EXPIRY_HOURS = 24
    settings.CHUNKED_UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024
    settings.CHUNKED_UPLOAD_TEMP_DIR = str(tmp_path / "chunks")
    # Ensure the directory exists
    os.makedirs(settings.CHUNKED_UPLOAD_TEMP_DIR, exist_ok=True)
