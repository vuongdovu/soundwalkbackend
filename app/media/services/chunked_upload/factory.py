"""
Factory function for chunked upload service backend selection.

Provides a single function to get the appropriate chunked upload service
based on the configured storage backend, mirroring the pattern used by
FileDeliveryService.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.files.storage import default_storage

if TYPE_CHECKING:
    from media.services.chunked_upload.base import ChunkedUploadServiceBase


def is_s3_storage() -> bool:
    """
    Check if the default storage backend is S3.

    Detects S3Boto3Storage by checking for the 'bucket' attribute,
    which is present on S3 storage backends but not on local FileSystemStorage.

    Returns:
        True if using S3 storage, False for local FileSystemStorage
    """
    return hasattr(default_storage, "bucket")


def get_chunked_upload_service() -> "ChunkedUploadServiceBase":
    """
    Get the appropriate chunked upload service for the current storage backend.

    Returns LocalChunkedUploadService for local filesystem storage
    and S3ChunkedUploadService for S3 storage.

    Returns:
        ChunkedUploadServiceBase implementation for the current backend

    Usage:
        service = get_chunked_upload_service()
        result = service.create_session(user, filename, file_size, mime_type, media_type)
    """
    if is_s3_storage():
        from media.services.chunked_upload.s3 import S3ChunkedUploadService

        return S3ChunkedUploadService()

    from media.services.chunked_upload.local import LocalChunkedUploadService

    return LocalChunkedUploadService()
