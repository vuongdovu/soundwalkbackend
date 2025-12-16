"""
Chunked upload service package.

Provides resumable, chunked uploads with support for both local filesystem
and S3 storage backends.

Usage:
    from media.services.chunked_upload import get_chunked_upload_service

    # Get the appropriate service based on storage backend
    service = get_chunked_upload_service()

    # Create upload session
    result = service.create_session(
        user=user,
        filename="video.mp4",
        file_size=1024*1024*100,
        mime_type="video/mp4",
        media_type="video",
    )

    if result.success:
        session = result.data
        # Get chunk upload target
        target = service.get_chunk_target(session, part_number=1)
        # ... client uploads chunk ...
        # Record completion
        service.record_completed_part(session, part_number=1, etag="...", size=5242880)
        # Finalize when all parts uploaded
        media_file_result = service.finalize_upload(session)
"""

from media.services.chunked_upload.base import (
    ChunkedUploadServiceBase,
    ChunkTarget,
    PartCompletionResult,
)
from media.services.chunked_upload.factory import (
    get_chunked_upload_service,
    is_s3_storage,
)
from media.services.chunked_upload.local import LocalChunkedUploadService

__all__ = [
    "ChunkedUploadServiceBase",
    "ChunkTarget",
    "PartCompletionResult",
    "get_chunked_upload_service",
    "is_s3_storage",
    "LocalChunkedUploadService",
]

# S3 service requires boto3, import conditionally
try:
    from media.services.chunked_upload.s3 import S3ChunkedUploadService  # noqa: F401

    __all__.append("S3ChunkedUploadService")
except ImportError:
    # boto3 not installed (local dev without S3)
    pass
