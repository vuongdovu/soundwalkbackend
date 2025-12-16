"""
S3 implementation of chunked upload service.

Handles chunked uploads using S3's multipart upload API:
- Creates multipart upload on session creation
- Generates presigned URLs for direct client uploads
- Tracks part completion for CompleteMultipartUpload
- Cleans up with AbortMultipartUpload on abort/expiry

Note: Requires boto3 to be installed. Import is done lazily to allow
the module to be loaded even when boto3 is not installed (local dev).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from celery import chain
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from core.services import ServiceResult
from media.models import MediaFile, UploadSession
from media.services.chunked_upload.base import (
    ChunkedUploadServiceBase,
    ChunkTarget,
    PartCompletionResult,
)
from media.tasks import process_media_file, scan_file_for_malware

if TYPE_CHECKING:
    from authentication.models import User


class S3ChunkedUploadService(ChunkedUploadServiceBase):
    """
    Chunked upload service for S3 storage using multipart upload.

    Clients upload directly to S3 using presigned URLs, then notify
    our server of completion. This reduces server bandwidth and
    enables parallel chunk uploads.
    """

    def __init__(
        self,
        bucket_name: str | None = None,
        presigned_url_expiry: int = 3600,  # 1 hour
        expiry_hours: int = 24,
        chunk_size: int = 5 * 1024 * 1024,  # 5MB (S3 minimum)
    ) -> None:
        """
        Initialize the S3 chunked upload service.

        Args:
            bucket_name: S3 bucket name. Defaults to storage bucket.
            presigned_url_expiry: Seconds until presigned URLs expire.
            expiry_hours: Hours until upload sessions expire.
            chunk_size: Size of each chunk in bytes.
        """
        self.bucket_name = bucket_name or getattr(
            default_storage, "bucket_name", "default-bucket"
        )
        self.presigned_url_expiry = presigned_url_expiry
        self.expiry_hours = expiry_hours
        self.chunk_size = chunk_size
        self._s3_client = None

    @property
    def s3_client(self):
        """Get or create S3 client."""
        if self._s3_client is None:
            import boto3

            self._s3_client = boto3.client("s3")
        return self._s3_client

    def _generate_s3_key(self, session_id: str, filename: str) -> str:
        """Generate S3 key for the upload."""
        # Use pending uploads path that can be moved after completion
        return f"uploads/pending/{session_id}/{filename}"

    def create_session(
        self,
        user: "User",
        filename: str,
        file_size: int,
        mime_type: str,
        media_type: str,
    ) -> ServiceResult[UploadSession]:
        """
        Create a new S3 multipart upload session.

        Validates quota and initiates S3 multipart upload.
        """
        # Check quota before creating session
        if hasattr(user, "profile") and not user.profile.can_upload(file_size):
            return ServiceResult.failure(
                "Storage quota exceeded. Please free up space or upgrade your plan."
            )

        # Generate session ID and S3 key
        session_id = uuid.uuid4()
        s3_key = self._generate_s3_key(str(session_id), filename)

        try:
            # Initiate S3 multipart upload
            response = self.s3_client.create_multipart_upload(
                Bucket=self.bucket_name,
                Key=s3_key,
                ContentType=mime_type,
            )
            upload_id = response["UploadId"]

            # Calculate expiration
            expires_at = timezone.now() + timedelta(hours=self.expiry_hours)

            # Create the session
            session = UploadSession.objects.create(
                id=session_id,
                uploader=user,
                filename=filename,
                file_size=file_size,
                mime_type=mime_type,
                media_type=media_type,
                backend=UploadSession.Backend.S3,
                s3_key=s3_key,
                s3_upload_id=upload_id,
                chunk_size=self.chunk_size,
                expires_at=expires_at,
            )

            return ServiceResult.success(session)

        except Exception as e:
            return ServiceResult.failure(
                f"Failed to create S3 multipart upload: {str(e)}"
            )

    def get_chunk_target(
        self,
        session: UploadSession,
        part_number: int,
    ) -> ServiceResult[ChunkTarget]:
        """
        Get presigned URL for uploading a chunk directly to S3.
        """
        # Check if session is expired
        if session.is_expired:
            return ServiceResult.failure("Upload session has expired.")

        # Validate part number
        if part_number < 1:
            return ServiceResult.failure("Part number must be at least 1.")

        if part_number > session.total_parts:
            return ServiceResult.failure(
                f"Part number {part_number} exceeds total parts ({session.total_parts})."
            )

        try:
            # Generate presigned URL for PUT operation
            presigned_url = self.s3_client.generate_presigned_url(
                ClientMethod="upload_part",
                Params={
                    "Bucket": self.bucket_name,
                    "Key": session.s3_key,
                    "UploadId": session.s3_upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=self.presigned_url_expiry,
            )

            return ServiceResult.success(
                ChunkTarget(
                    upload_url=presigned_url,
                    part_number=part_number,
                    method="PUT",
                    direct=True,  # Client uploads directly to S3
                    expires_in=self.presigned_url_expiry,
                )
            )

        except Exception as e:
            return ServiceResult.failure(f"Failed to generate presigned URL: {str(e)}")

    def record_completed_part(
        self,
        session: UploadSession,
        part_number: int,
        etag: str,
        size: int,
    ) -> ServiceResult[PartCompletionResult]:
        """
        Record that a part was uploaded to S3.

        Stores the ETag which is required for CompleteMultipartUpload.
        """
        # Check if this part already exists
        existing_parts = {p["part_number"]: p for p in session.parts_completed}

        if part_number not in existing_parts:
            # Add new part
            session.parts_completed.append(
                {
                    "part_number": part_number,
                    "etag": etag,
                    "size": size,
                }
            )
            session.bytes_received += size
        else:
            # Part already recorded - update if needed
            old_size = existing_parts[part_number]["size"]
            for part in session.parts_completed:
                if part["part_number"] == part_number:
                    part["etag"] = etag
                    part["size"] = size
                    break
            session.bytes_received = session.bytes_received - old_size + size

        # Sort parts by part number
        session.parts_completed.sort(key=lambda p: p["part_number"])
        session.save()

        is_complete = session.bytes_received >= session.file_size

        return ServiceResult.success(
            PartCompletionResult(
                bytes_received=session.bytes_received,
                parts_completed=len(session.parts_completed),
                is_complete=is_complete,
            )
        )

    def receive_chunk(
        self,
        session: UploadSession,
        part_number: int,
        data: bytes,
    ) -> ServiceResult[PartCompletionResult]:
        """
        Not supported for S3 - clients upload directly to S3.
        """
        return ServiceResult.failure(
            "S3 chunked upload does not support receive_chunk. "
            "Clients upload directly to S3 using presigned URLs."
        )

    def finalize_upload(
        self,
        session: UploadSession,
    ) -> ServiceResult[MediaFile]:
        """
        Complete the S3 multipart upload and create MediaFile.

        Calls CompleteMultipartUpload, creates MediaFile record,
        updates quota, and triggers processing pipeline.
        """
        # Validate session status
        if session.status != UploadSession.Status.IN_PROGRESS:
            return ServiceResult.failure(
                f"Cannot finalize session with status '{session.status}'."
            )

        # Check all parts are present
        completed_part_numbers = {p["part_number"] for p in session.parts_completed}
        expected_parts = set(range(1, session.total_parts + 1))
        missing_parts = expected_parts - completed_part_numbers

        if missing_parts:
            return ServiceResult.failure(
                f"Missing parts: {sorted(missing_parts)}. Upload is incomplete."
            )

        try:
            with transaction.atomic():
                # Build parts list for S3 completion
                # Parts must be sorted by part number
                parts = sorted(session.parts_completed, key=lambda p: p["part_number"])
                s3_parts = [
                    {"ETag": p["etag"], "PartNumber": p["part_number"]} for p in parts
                ]

                # Complete the multipart upload
                self.s3_client.complete_multipart_upload(
                    Bucket=self.bucket_name,
                    Key=session.s3_key,
                    UploadId=session.s3_upload_id,
                    MultipartUpload={"Parts": s3_parts},
                )

                # Create MediaFile pointing to S3 location
                # We use the S3 key directly since the file is already in S3
                media_file = MediaFile(
                    original_filename=session.filename,
                    media_type=session.media_type,
                    mime_type=session.mime_type,
                    file_size=session.file_size,
                    uploader=session.uploader,
                    version=1,
                    is_current=True,
                )
                # Set the file field to the S3 key
                media_file.file.name = session.s3_key
                media_file.save()

                # Update user's storage quota
                if hasattr(session.uploader, "profile"):
                    session.uploader.profile.add_storage_usage(session.file_size)

                # Mark session as completed
                session.status = UploadSession.Status.COMPLETED
                session.save()

            # Trigger processing pipeline (outside transaction)
            chain(
                scan_file_for_malware.s(str(media_file.id)),
                process_media_file.s(),
            ).delay()

            return ServiceResult.success(media_file)

        except Exception as e:
            return ServiceResult.failure(f"Failed to finalize upload: {str(e)}")

    def abort_upload(
        self,
        session: UploadSession,
    ) -> ServiceResult[None]:
        """
        Abort the S3 multipart upload and clean up.
        """
        # Cannot abort a completed session
        if session.status == UploadSession.Status.COMPLETED:
            return ServiceResult.failure("Cannot abort a completed session.")

        try:
            # Abort the S3 multipart upload
            self.s3_client.abort_multipart_upload(
                Bucket=self.bucket_name,
                Key=session.s3_key,
                UploadId=session.s3_upload_id,
            )

            # Mark session as failed
            session.status = UploadSession.Status.FAILED
            session.save()

            return ServiceResult.success(None)

        except Exception as e:
            # Still mark the session as failed even if S3 cleanup fails
            session.status = UploadSession.Status.FAILED
            session.save()
            return ServiceResult.failure(
                f"Warning: S3 cleanup may have failed: {str(e)}"
            )

    def get_session_progress(
        self,
        session: UploadSession,
    ) -> dict:
        """
        Get current progress information for a session.
        """
        progress_percent = 0.0
        if session.file_size > 0:
            progress_percent = min(
                100.0, (session.bytes_received / session.file_size) * 100
            )

        return {
            "session_id": str(session.id),
            "filename": session.filename,
            "file_size": session.file_size,
            "bytes_received": session.bytes_received,
            "parts_completed": len(session.parts_completed),
            "total_parts": session.total_parts,
            "progress_percent": progress_percent,
            "status": session.status,
        }
