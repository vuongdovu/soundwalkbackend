"""
Local filesystem implementation of chunked upload service.

Handles chunked uploads by:
- Writing chunks to a temporary directory
- Concatenating chunks on finalization
- Moving the assembled file to Django's storage
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from celery import chain
from django.conf import settings
from django.core.files.base import ContentFile
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


class LocalChunkedUploadService(ChunkedUploadServiceBase):
    """
    Chunked upload service for local filesystem storage.

    Chunks are stored in a temporary directory and concatenated on finalization.
    This approach allows for resumable uploads and reduces memory usage for large files.
    """

    def __init__(
        self,
        temp_base_dir: str | None = None,
        expiry_hours: int = 24,
        chunk_size: int = 5 * 1024 * 1024,  # 5MB default
    ) -> None:
        """
        Initialize the local chunked upload service.

        Args:
            temp_base_dir: Base directory for temp chunk storage.
                           Defaults to MEDIA_ROOT/chunks.
            expiry_hours: Hours until upload sessions expire.
            chunk_size: Size of each chunk in bytes.
        """
        self.temp_base_dir = temp_base_dir or os.path.join(
            settings.MEDIA_ROOT, "chunks"
        )
        self.expiry_hours = expiry_hours
        self.chunk_size = chunk_size

    def create_session(
        self,
        user: "User",
        filename: str,
        file_size: int,
        mime_type: str,
        media_type: str,
    ) -> ServiceResult[UploadSession]:
        """
        Create a new local chunked upload session.

        Validates quota, creates a temp directory, and returns the session.
        """
        # Check quota before creating session
        if hasattr(user, "profile") and not user.profile.can_upload(file_size):
            return ServiceResult.failure(
                "Storage quota exceeded. Please free up space or upgrade your plan."
            )

        # Generate session ID and temp directory
        session_id = uuid.uuid4()
        temp_dir = os.path.join(self.temp_base_dir, str(session_id))

        # Create temp directory
        os.makedirs(temp_dir, exist_ok=True)

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
            backend=UploadSession.Backend.LOCAL,
            local_temp_dir=temp_dir,
            chunk_size=self.chunk_size,
            expires_at=expires_at,
        )

        return ServiceResult.success(session)

    def get_chunk_target(
        self,
        session: UploadSession,
        part_number: int,
    ) -> ServiceResult[ChunkTarget]:
        """
        Get the upload target for a specific chunk.

        For local storage, returns an endpoint URL pointing to our server.
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

        # Build the upload URL (server endpoint)
        upload_url = f"/api/v1/media/chunked/sessions/{session.id}/parts/{part_number}/"

        return ServiceResult.success(
            ChunkTarget(
                upload_url=upload_url,
                part_number=part_number,
                method="PUT",
                direct=False,  # Upload goes through our server
            )
        )

    def record_completed_part(
        self,
        session: UploadSession,
        part_number: int,
        etag: str,
        size: int,
    ) -> ServiceResult[PartCompletionResult]:
        """
        Record that a part has been successfully uploaded.

        For local storage, this is handled by receive_chunk, so this is mainly
        for S3 compatibility. If called, it will update the session with the
        provided information.
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
            # Part already recorded, skip
            pass

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
        Receive and store a chunk.

        Writes the chunk data to a part file in the temp directory.
        """
        # Validate session status
        if session.status != UploadSession.Status.IN_PROGRESS:
            return ServiceResult.failure(
                f"Cannot upload to session with status '{session.status}'."
            )

        # Check if session is expired
        if session.is_expired:
            return ServiceResult.failure("Upload session has expired.")

        # Validate part number
        if part_number < 1 or part_number > session.total_parts:
            return ServiceResult.failure(
                f"Invalid part number {part_number}. Must be between 1 and {session.total_parts}."
            )

        # Write chunk to temp directory
        part_filename = f"part_{part_number:04d}"
        part_path = Path(session.local_temp_dir) / part_filename
        part_path.write_bytes(data)

        # Calculate ETag (MD5 hash of content)
        etag = hashlib.md5(data).hexdigest()

        # Check if this part was already recorded
        existing_parts = {p["part_number"]: p for p in session.parts_completed}

        if part_number not in existing_parts:
            # Add new part
            session.parts_completed.append(
                {
                    "part_number": part_number,
                    "etag": etag,
                    "size": len(data),
                }
            )
            session.bytes_received += len(data)
        else:
            # Part already uploaded, update the file but don't double-count bytes
            old_size = existing_parts[part_number]["size"]
            # Update the existing part entry
            for part in session.parts_completed:
                if part["part_number"] == part_number:
                    part["etag"] = etag
                    part["size"] = len(data)
                    break
            # Adjust bytes_received if size changed
            session.bytes_received = session.bytes_received - old_size + len(data)

        session.save()

        is_complete = session.bytes_received >= session.file_size

        return ServiceResult.success(
            PartCompletionResult(
                bytes_received=session.bytes_received,
                parts_completed=len(session.parts_completed),
                is_complete=is_complete,
            )
        )

    def finalize_upload(
        self,
        session: UploadSession,
    ) -> ServiceResult[MediaFile]:
        """
        Complete the upload and create the MediaFile.

        Assembles chunks into the final file, creates MediaFile,
        updates quota, triggers processing pipeline, and cleans up.
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
                # Assemble chunks into final file
                temp_dir = Path(session.local_temp_dir)
                assembled_content = bytearray()

                for part_num in range(1, session.total_parts + 1):
                    part_path = temp_dir / f"part_{part_num:04d}"
                    assembled_content.extend(part_path.read_bytes())

                # Create ContentFile for Django storage
                content_file = ContentFile(
                    bytes(assembled_content), name=session.filename
                )

                # Create MediaFile
                media_file = MediaFile(
                    file=content_file,
                    original_filename=session.filename,
                    media_type=session.media_type,
                    mime_type=session.mime_type,
                    file_size=session.file_size,
                    uploader=session.uploader,
                    version=1,
                    is_current=True,
                )
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

            # Clean up temp directory
            if os.path.exists(session.local_temp_dir):
                shutil.rmtree(session.local_temp_dir)

            return ServiceResult.success(media_file)

        except Exception as e:
            return ServiceResult.failure(f"Failed to finalize upload: {str(e)}")

    def abort_upload(
        self,
        session: UploadSession,
    ) -> ServiceResult[None]:
        """
        Abort an in-progress upload and clean up resources.

        Deletes the temp directory and marks the session as failed.
        """
        # Cannot abort a completed session
        if session.status == UploadSession.Status.COMPLETED:
            return ServiceResult.failure("Cannot abort a completed session.")

        # Mark session as failed
        session.status = UploadSession.Status.FAILED
        session.save()

        # Clean up temp directory
        if session.local_temp_dir and os.path.exists(session.local_temp_dir):
            shutil.rmtree(session.local_temp_dir)

        return ServiceResult.success(None)

    def get_session_progress(
        self,
        session: UploadSession,
    ) -> dict:
        """
        Get current progress information for a session.

        Returns a dictionary with all progress details.
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
