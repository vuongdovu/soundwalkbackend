"""
Test fixtures for media app.

Provides fixtures for:
- Sample files (JPEG, PNG, PDF, etc.)
- Invalid files (executable, empty, oversized)
- Authenticated test clients
- Users with storage quotas
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.tests.factories import UserFactory

if TYPE_CHECKING:
    from authentication.models import User


# =============================================================================
# API Client Fixtures
# =============================================================================


@pytest.fixture
def api_client() -> APIClient:
    """Return unauthenticated API client."""
    return APIClient()


@pytest.fixture
def user(db) -> "User":
    """Create a verified user with default storage quota."""
    user = UserFactory(email_verified=True)
    # Ensure profile has storage quota
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB
    user.profile.total_storage_bytes = 0
    user.profile.save()
    return user


@pytest.fixture
def user_near_quota(db) -> "User":
    """Create a user near their storage quota (only 1MB remaining)."""
    user = UserFactory(email_verified=True)
    user.profile.storage_quota_bytes = 100 * 1024 * 1024  # 100MB
    user.profile.total_storage_bytes = 99 * 1024 * 1024  # 99MB used
    user.profile.save()
    return user


@pytest.fixture
def authenticated_client(user: "User") -> APIClient:
    """Return API client authenticated with JWT token."""
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def authenticated_client_near_quota(user_near_quota: "User") -> APIClient:
    """Return authenticated client for user near quota."""
    client = APIClient()
    refresh = RefreshToken.for_user(user_near_quota)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


# =============================================================================
# Valid File Fixtures
# =============================================================================


@pytest.fixture
def sample_jpeg() -> io.BytesIO:
    """Generate a valid JPEG image file."""
    image = Image.new("RGB", (100, 100), color="red")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    buffer.name = "test_image.jpg"
    return buffer


@pytest.fixture
def sample_jpeg_uploaded(sample_jpeg: io.BytesIO) -> SimpleUploadedFile:
    """Return JPEG as SimpleUploadedFile."""
    return SimpleUploadedFile(
        name="test_image.jpg",
        content=sample_jpeg.read(),
        content_type="image/jpeg",
    )


@pytest.fixture
def sample_png() -> io.BytesIO:
    """Generate a valid PNG image file with transparency."""
    image = Image.new("RGBA", (100, 100), color=(0, 0, 255, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    buffer.name = "test_image.png"
    return buffer


@pytest.fixture
def sample_png_uploaded(sample_png: io.BytesIO) -> SimpleUploadedFile:
    """Return PNG as SimpleUploadedFile."""
    return SimpleUploadedFile(
        name="test_image.png",
        content=sample_png.read(),
        content_type="image/png",
    )


@pytest.fixture
def sample_gif() -> io.BytesIO:
    """Generate a valid GIF image file."""
    image = Image.new("P", (50, 50), color=1)
    buffer = io.BytesIO()
    image.save(buffer, format="GIF")
    buffer.seek(0)
    buffer.name = "test_image.gif"
    return buffer


@pytest.fixture
def sample_pdf() -> io.BytesIO:
    """Generate a minimal valid PDF file."""
    # Minimal PDF structure that is recognized by libmagic
    pdf_content = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj
xref
0 3
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
trailer << /Size 3 /Root 1 0 R >>
startxref
110
%%EOF"""
    buffer = io.BytesIO(pdf_content)
    buffer.name = "test_document.pdf"
    return buffer


@pytest.fixture
def sample_pdf_uploaded(sample_pdf: io.BytesIO) -> SimpleUploadedFile:
    """Return PDF as SimpleUploadedFile."""
    return SimpleUploadedFile(
        name="test_document.pdf",
        content=sample_pdf.read(),
        content_type="application/pdf",
    )


@pytest.fixture
def sample_txt() -> io.BytesIO:
    """Generate a plain text file."""
    content = b"This is a test text file.\nWith multiple lines.\n"
    buffer = io.BytesIO(content)
    buffer.name = "test_document.txt"
    return buffer


@pytest.fixture
def sample_txt_uploaded(sample_txt: io.BytesIO) -> SimpleUploadedFile:
    """Return text file as SimpleUploadedFile."""
    return SimpleUploadedFile(
        name="test_document.txt",
        content=sample_txt.read(),
        content_type="text/plain",
    )


# =============================================================================
# Invalid File Fixtures
# =============================================================================


@pytest.fixture
def empty_file() -> io.BytesIO:
    """Generate an empty file."""
    buffer = io.BytesIO(b"")
    buffer.name = "empty.txt"
    return buffer


@pytest.fixture
def empty_file_uploaded() -> SimpleUploadedFile:
    """Return empty file as SimpleUploadedFile."""
    return SimpleUploadedFile(
        name="empty.txt",
        content=b"",
        content_type="text/plain",
    )


@pytest.fixture
def executable_file() -> io.BytesIO:
    """Generate a file that looks like an ELF executable."""
    # ELF magic bytes followed by padding
    elf_header = b"\x7fELF" + b"\x01\x01\x01\x00" + b"\x00" * 100
    buffer = io.BytesIO(elf_header)
    buffer.name = "malware.jpg"  # Fake .jpg extension
    return buffer


@pytest.fixture
def executable_file_uploaded(executable_file: io.BytesIO) -> SimpleUploadedFile:
    """Return executable as SimpleUploadedFile with fake jpg extension."""
    return SimpleUploadedFile(
        name="totally_not_malware.jpg",
        content=executable_file.read(),
        content_type="image/jpeg",  # Fake content type
    )


@pytest.fixture
def oversized_image() -> io.BytesIO:
    """
    Create a file that exceeds the 25MB image size limit.

    Note: This creates a 26MB file by padding a valid JPEG.
    The file will still be recognized as JPEG by libmagic.
    """
    # Create a small valid JPEG first
    image = Image.new("RGB", (100, 100), color="red")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")

    # Get the JPEG data and pad it to exceed 25MB
    jpeg_data = buffer.getvalue()
    # Add padding bytes at the end (after JPEG EOI marker)
    padding_size = (26 * 1024 * 1024) - len(jpeg_data)
    padded_data = jpeg_data + (b"\x00" * padding_size)

    result = io.BytesIO(padded_data)
    result.name = "large_image.jpg"
    return result


@pytest.fixture
def oversized_image_uploaded(oversized_image: io.BytesIO) -> SimpleUploadedFile:
    """Return oversized image as SimpleUploadedFile."""
    content = oversized_image.read()
    return SimpleUploadedFile(
        name="large_image.jpg",
        content=content,
        content_type="image/jpeg",
    )


# =============================================================================
# Extension Mismatch Fixtures
# =============================================================================


@pytest.fixture
def png_with_jpg_extension(sample_png: io.BytesIO) -> io.BytesIO:
    """PNG file with .jpg extension (extension mismatch)."""
    sample_png.seek(0)
    content = sample_png.read()
    buffer = io.BytesIO(content)
    buffer.name = "actually_a_png.jpg"
    return buffer


@pytest.fixture
def pdf_with_txt_extension(sample_pdf: io.BytesIO) -> io.BytesIO:
    """PDF file with .txt extension (extension mismatch)."""
    sample_pdf.seek(0)
    content = sample_pdf.read()
    buffer = io.BytesIO(content)
    buffer.name = "actually_a_pdf.txt"
    return buffer


# =============================================================================
# Malware Scanning Fixtures
# =============================================================================


# EICAR test string - standard antivirus test signature
# This is NOT malware - it's an industry-standard test pattern
EICAR_TEST_STRING = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


@pytest.fixture
def eicar_test_file() -> io.BytesIO:
    """
    Generate EICAR test file for antivirus testing.

    The EICAR test string is a standard pattern recognized
    by all antivirus software as a test signature. It is
    NOT actual malware.
    """
    buffer = io.BytesIO(EICAR_TEST_STRING)
    buffer.name = "eicar_test.txt"
    return buffer


@pytest.fixture
def eicar_test_file_uploaded(eicar_test_file: io.BytesIO) -> SimpleUploadedFile:
    """Return EICAR test file as SimpleUploadedFile."""
    return SimpleUploadedFile(
        name="eicar_test.txt",
        content=eicar_test_file.read(),
        content_type="text/plain",
    )


@pytest.fixture
def mock_clamav_scanner():
    """
    Mock ClamAV scanner for unit tests.

    Provides a mock that simulates ClamAV behavior without
    requiring an actual ClamAV daemon.
    """
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.ping.return_value = True
    mock.scan_stream.return_value = None  # Clean by default
    mock.scan_file.return_value = None  # Clean by default
    mock.version.return_value = "ClamAV 1.2.0/27234/Mon Dec 11 09:26:33 2024"
    return mock


@pytest.fixture
def media_file_pending_scan(user: "User", sample_jpeg_uploaded: SimpleUploadedFile, db):
    """
    Create a MediaFile awaiting scan.

    The file is in PENDING scan_status, as if just uploaded.
    """
    from pathlib import Path
    from django.conf import settings
    from media.models import MediaFile

    # Create directory for test file
    media_root = Path(settings.MEDIA_ROOT)
    test_dir = media_root / "test_scans"
    test_dir.mkdir(parents=True, exist_ok=True)

    # Write file to disk
    file_path = test_dir / "test_scan_file.jpg"
    file_path.write_bytes(sample_jpeg_uploaded.read())
    sample_jpeg_uploaded.seek(0)

    # Create MediaFile
    media_file = MediaFile.objects.create(
        file="test_scans/test_scan_file.jpg",
        original_filename="test_image.jpg",
        media_type=MediaFile.MediaType.IMAGE,
        mime_type="image/jpeg",
        file_size=sample_jpeg_uploaded.size,
        uploader=user,
        visibility=MediaFile.Visibility.PRIVATE,
        scan_status=MediaFile.ScanStatus.PENDING,
        processing_status=MediaFile.ProcessingStatus.PENDING,
    )

    yield media_file

    # Cleanup
    import shutil

    if file_path.exists():
        file_path.unlink()
    if test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)
