"""
Base types and abstract base class for chunked upload services.

This module defines the interface that all chunked upload service implementations
must follow, ensuring consistent behavior across local and S3 backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from authentication.models import User
    from media.models import MediaFile, UploadSession

    from core.services import ServiceResult


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class ChunkTarget:
    """
    Information about where and how to upload a chunk.

    Attributes:
        upload_url: URL to upload the chunk to
        part_number: The part number (1-indexed)
        method: HTTP method to use ('PUT')
        direct: True if client uploads directly to URL (S3),
                False if upload goes through our server (local)
        expires_in: Seconds until the URL expires (for presigned URLs)
        headers: Optional headers to include with the upload request
    """

    upload_url: str
    part_number: int
    method: str = "PUT"
    direct: bool = False
    expires_in: int | None = None
    headers: dict[str, str] | None = None


@dataclass
class PartCompletionResult:
    """
    Result of recording a completed part upload.

    Attributes:
        bytes_received: Total bytes received across all parts
        parts_completed: Number of parts that have been completed
        is_complete: True if all expected bytes have been received
    """

    bytes_received: int
    parts_completed: int
    is_complete: bool


# =============================================================================
# Abstract Base Class
# =============================================================================


class ChunkedUploadServiceBase(ABC):
    """
    Abstract base class for chunked upload services.

    Implementations must provide backend-specific logic for:
    - Session initialization (creating temp storage, S3 multipart upload)
    - Chunk target generation (local endpoint vs S3 presigned URL)
    - Part completion tracking
    - File assembly and MediaFile creation on finalization
    - Cleanup on abort or expiration

    The interface is designed to be consistent from the API consumer's
    perspective regardless of the storage backend.
    """

    @abstractmethod
    def create_session(
        self,
        user: "User",
        filename: str,
        file_size: int,
        mime_type: str,
        media_type: str,
    ) -> "ServiceResult[UploadSession]":
        """
        Create a new chunked upload session.

        This initializes the upload, checking quota and creating any
        backend-specific resources (temp directory for local, multipart
        upload for S3).

        Args:
            user: The user initiating the upload
            filename: Original filename
            file_size: Expected total file size in bytes
            mime_type: MIME type of the file
            media_type: Media type category (image, video, document, audio, other)

        Returns:
            ServiceResult containing the created UploadSession on success,
            or an error if quota exceeded or validation failed.
        """

    @abstractmethod
    def get_chunk_target(
        self,
        session: "UploadSession",
        part_number: int,
    ) -> "ServiceResult[ChunkTarget]":
        """
        Get the upload target for a specific chunk.

        For local storage, this returns an endpoint URL on our server.
        For S3, this returns a presigned URL for direct upload.

        Args:
            session: The upload session
            part_number: Part number to upload (1-indexed)

        Returns:
            ServiceResult containing ChunkTarget with upload URL and metadata.
        """

    @abstractmethod
    def record_completed_part(
        self,
        session: "UploadSession",
        part_number: int,
        etag: str,
        size: int,
    ) -> "ServiceResult[PartCompletionResult]":
        """
        Record that a part has been successfully uploaded.

        For S3, this stores the ETag needed for CompleteMultipartUpload.
        For local, this may be a no-op if the write itself is the record.

        Args:
            session: The upload session
            part_number: The completed part number (1-indexed)
            etag: ETag/checksum of the uploaded part
            size: Size of the uploaded part in bytes

        Returns:
            ServiceResult containing progress information.
        """

    @abstractmethod
    def receive_chunk(
        self,
        session: "UploadSession",
        part_number: int,
        data: bytes,
    ) -> "ServiceResult[PartCompletionResult]":
        """
        Receive and store a chunk (local backend only).

        This method is called when a client uploads a chunk through our server.
        For S3, clients upload directly to S3, so this is not used.

        Args:
            session: The upload session
            part_number: The part number (1-indexed)
            data: The chunk data

        Returns:
            ServiceResult containing progress information.
        """

    @abstractmethod
    def finalize_upload(
        self,
        session: "UploadSession",
    ) -> "ServiceResult[MediaFile]":
        """
        Complete the upload and create the MediaFile.

        For local storage, this assembles chunks into the final file.
        For S3, this calls CompleteMultipartUpload.

        This also:
        - Updates the user's storage quota
        - Triggers the scan/process pipeline
        - Marks the session as completed
        - Cleans up temporary resources

        Args:
            session: The upload session

        Returns:
            ServiceResult containing the created MediaFile.
        """

    @abstractmethod
    def abort_upload(
        self,
        session: "UploadSession",
    ) -> "ServiceResult[None]":
        """
        Abort an in-progress upload and clean up resources.

        For local storage, this deletes the temp directory.
        For S3, this calls AbortMultipartUpload.

        Args:
            session: The upload session

        Returns:
            ServiceResult indicating success or failure.
        """

    @abstractmethod
    def get_session_progress(
        self,
        session: "UploadSession",
    ) -> dict:
        """
        Get current progress information for a session.

        Args:
            session: The upload session

        Returns:
            Dictionary with progress information:
            - session_id: UUID of the session
            - filename: Original filename
            - file_size: Expected total size
            - bytes_received: Bytes received so far
            - parts_completed: Number of completed parts
            - total_parts: Total parts needed
            - progress_percent: Percentage complete (0-100)
            - status: Current session status
        """
