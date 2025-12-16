"""
API views for media file uploads and protected access.

Provides:
- MediaUploadView: Handle file uploads via POST
- MediaFileDetailView: Get file details with access control
- MediaFileDownloadView: Download file with access control
- MediaFileViewView: View file inline with access control
- MediaFileShareView: Manage shares for a file
- MediaFileSharesReceivedView: List files shared with current user
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from media.models import MediaFile
from media.models import UploadSession
from media.serializers import (
    ChunkedUploadFinalizeResultSerializer,
    ChunkedUploadInitSerializer,
    ChunkedUploadProgressSerializer,
    ChunkedUploadSessionSerializer,
    ChunkTargetSerializer,
    MediaFileSerializer,
    MediaFileShareCreateSerializer,
    MediaFileShareSerializer,
    MediaFileUploadSerializer,
    PartCompletionResultSerializer,
    QuotaStatusSerializer,
)
from media.services.access_control import AccessControlService, FileAccessLevel
from media.services.chunked_upload import get_chunked_upload_service
from media.services.delivery import FileDeliveryService


class MediaUploadView(APIView):
    """
    Handle media file uploads.

    POST /api/v1/media/upload/
        Upload a new media file.

    Authentication:
        Requires valid JWT token.

    Request:
        Content-Type: multipart/form-data
        - file (required): The file to upload
        - visibility (optional): Access level (private, shared, internal)

    Response:
        201 Created: File uploaded successfully
        400 Bad Request: Validation error (invalid file type, size, quota)
        401 Unauthorized: Not authenticated
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="Upload media file",
        description=(
            "Upload a new media file. Validates MIME type from content, "
            "enforces size limits, and checks storage quota."
        ),
        request=MediaFileUploadSerializer,
        responses={
            201: OpenApiResponse(
                response=MediaFileSerializer,
                description="File uploaded successfully",
            ),
            400: OpenApiResponse(
                description="Validation error",
            ),
            401: OpenApiResponse(
                description="Not authenticated",
            ),
        },
        tags=["Media"],
    )
    def post(self, request):
        """
        Upload a media file.

        Args:
            request: HTTP request with multipart file data.

        Returns:
            Response with created media file data or validation errors.
        """
        upload_serializer = MediaFileUploadSerializer(
            data=request.data,
            context={"request": request},
        )

        if not upload_serializer.is_valid():
            return Response(
                upload_serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        media_file = upload_serializer.save()

        output_serializer = MediaFileSerializer(
            media_file,
            context={"request": request},
        )

        return Response(
            output_serializer.data,
            status=status.HTTP_201_CREATED,
        )


class MediaFileDetailView(APIView):
    """
    Get media file details with access control.

    GET /api/v1/media/files/{file_id}/
        Get file metadata if user has VIEW access or higher.

    Authentication:
        Requires valid JWT token.

    Response:
        200 OK: File details
        403 Forbidden: User doesn't have access
        404 Not Found: File doesn't exist
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get media file details",
        description="Get metadata for a media file. Requires at least VIEW access.",
        responses={
            200: OpenApiResponse(
                response=MediaFileSerializer,
                description="File details",
            ),
            403: OpenApiResponse(description="Access denied"),
            404: OpenApiResponse(description="File not found"),
        },
        tags=["Media"],
    )
    def get(self, request, file_id):
        """Get media file details."""
        try:
            media_file = MediaFile.objects.get(pk=file_id)
        except MediaFile.DoesNotExist:
            return Response(
                {"error": "File not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not AccessControlService.user_can_access(request.user, media_file):
            return Response(
                {"error": "You don't have access to this file"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MediaFileSerializer(media_file, context={"request": request})
        return Response(serializer.data)


class MediaFileDownloadView(APIView):
    """
    Download a media file with access control.

    GET /api/v1/media/files/{file_id}/download/
        Download file if user has DOWNLOAD access or higher.

    Authentication:
        Requires valid JWT token.

    Response:
        200 OK: File content (FileResponse or X-Accel-Redirect)
        403 Forbidden: User doesn't have download access
        404 Not Found: File doesn't exist
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Download media file",
        description="Download a media file. Requires DOWNLOAD access.",
        responses={
            200: OpenApiResponse(description="File content"),
            403: OpenApiResponse(description="Access denied"),
            404: OpenApiResponse(description="File not found"),
        },
        tags=["Media"],
    )
    def get(self, request, file_id):
        """Download a media file."""
        try:
            media_file = MediaFile.objects.get(pk=file_id)
        except MediaFile.DoesNotExist:
            return Response(
                {"error": "File not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not AccessControlService.user_can_download(request.user, media_file):
            return Response(
                {"error": "You don't have permission to download this file"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            return FileDeliveryService.serve_file_response(
                media_file,
                as_attachment=True,
            )
        except FileNotFoundError:
            return Response(
                {"error": "File not found on storage"},
                status=status.HTTP_404_NOT_FOUND,
            )


class MediaFileViewView(APIView):
    """
    View a media file inline (browser display) with access control.

    GET /api/v1/media/files/{file_id}/view/
        View file inline if user has VIEW access or higher.

    Authentication:
        Requires valid JWT token.

    Response:
        200 OK: File content (inline disposition)
        403 Forbidden: User doesn't have access
        404 Not Found: File doesn't exist
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="View media file inline",
        description="View a media file in browser. Requires VIEW access.",
        responses={
            200: OpenApiResponse(description="File content (inline)"),
            403: OpenApiResponse(description="Access denied"),
            404: OpenApiResponse(description="File not found"),
        },
        tags=["Media"],
    )
    def get(self, request, file_id):
        """View a media file inline."""
        try:
            media_file = MediaFile.objects.get(pk=file_id)
        except MediaFile.DoesNotExist:
            return Response(
                {"error": "File not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not AccessControlService.user_can_access(request.user, media_file):
            return Response(
                {"error": "You don't have access to this file"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            return FileDeliveryService.serve_file_response(
                media_file,
                as_attachment=False,
            )
        except FileNotFoundError:
            return Response(
                {"error": "File not found on storage"},
                status=status.HTTP_404_NOT_FOUND,
            )


class MediaFileShareView(APIView):
    """
    Manage shares for a media file.

    GET /api/v1/media/files/{file_id}/shares/
        List all shares for this file (owner only).

    POST /api/v1/media/files/{file_id}/shares/
        Create a new share (owner only).

    DELETE /api/v1/media/files/{file_id}/shares/{user_id}/
        Revoke a share (owner only).

    Authentication:
        Requires valid JWT token.
        Only the file owner can manage shares.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List file shares",
        description="List all active shares for a file. Owner only.",
        responses={
            200: OpenApiResponse(
                response=MediaFileShareSerializer(many=True),
                description="List of shares",
            ),
            403: OpenApiResponse(description="Not the file owner"),
            404: OpenApiResponse(description="File not found"),
        },
        tags=["Media Sharing"],
    )
    def get(self, request, file_id):
        """List all shares for a file."""
        try:
            media_file = MediaFile.objects.get(pk=file_id)
        except MediaFile.DoesNotExist:
            return Response(
                {"error": "File not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Only owner can view shares
        access_level = AccessControlService.get_access_level(request.user, media_file)
        if access_level != FileAccessLevel.OWNER:
            return Response(
                {"error": "Only the file owner can view shares"},
                status=status.HTTP_403_FORBIDDEN,
            )

        shares = AccessControlService.get_file_shares(media_file)
        serializer = MediaFileShareSerializer(shares, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Create file share",
        description="Share a file with another user. Owner only.",
        request=MediaFileShareCreateSerializer,
        responses={
            201: OpenApiResponse(
                response=MediaFileShareSerializer,
                description="Share created",
            ),
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(description="Not the file owner"),
            404: OpenApiResponse(description="File not found"),
        },
        tags=["Media Sharing"],
    )
    def post(self, request, file_id):
        """Create a new share for a file."""
        try:
            media_file = MediaFile.objects.get(pk=file_id)
        except MediaFile.DoesNotExist:
            return Response(
                {"error": "File not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = MediaFileShareCreateSerializer(
            data=request.data,
            context={"request": request, "media_file": media_file},
        )

        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        share = serializer.save()
        output_serializer = MediaFileShareSerializer(share)

        return Response(
            output_serializer.data,
            status=status.HTTP_201_CREATED,
        )


class MediaFileShareDeleteView(APIView):
    """
    Revoke a share for a media file.

    DELETE /api/v1/media/files/{file_id}/shares/{user_id}/
        Revoke a share (owner only).

    Authentication:
        Requires valid JWT token.
        Only the file owner can revoke shares.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Revoke file share",
        description="Revoke a user's access to a file. Owner only.",
        responses={
            204: OpenApiResponse(description="Share revoked"),
            403: OpenApiResponse(description="Not the file owner"),
            404: OpenApiResponse(description="File or share not found"),
        },
        tags=["Media Sharing"],
    )
    def delete(self, request, file_id, user_id):
        """Revoke a share."""
        from authentication.models import User

        try:
            media_file = MediaFile.objects.get(pk=file_id)
        except MediaFile.DoesNotExist:
            return Response(
                {"error": "File not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            recipient = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = AccessControlService.revoke_share(
            media_file=media_file,
            owner=request.user,
            recipient=recipient,
        )

        if not result.success:
            if result.error_code == "NOT_OWNER":
                return Response(
                    {"error": result.error},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response(
                {"error": result.error},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class MediaFilesSharedWithMeView(APIView):
    """
    List files shared with the current user.

    GET /api/v1/media/shared-with-me/
        Get all files that have been shared with the current user.

    Authentication:
        Requires valid JWT token.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List files shared with me",
        description="Get all files that have been shared with the current user.",
        responses={
            200: OpenApiResponse(
                response=MediaFileSerializer(many=True),
                description="List of shared files",
            ),
        },
        tags=["Media Sharing"],
    )
    def get(self, request):
        """List files shared with the current user."""
        files = AccessControlService.get_files_shared_with_user(request.user)
        serializer = MediaFileSerializer(
            files,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data)


# =============================================================================
# Chunked Upload Views
# =============================================================================


class ChunkedUploadSessionView(APIView):
    """
    Create a new chunked upload session.

    POST /api/v1/media/chunked/sessions/
        Initialize a new chunked upload session.

    Authentication:
        Requires valid JWT token.

    Request:
        - filename (required): Original filename
        - file_size (required): Total file size in bytes
        - mime_type (required): MIME type of the file

    Response:
        201 Created: Session created with upload instructions
        400 Bad Request: Validation error or quota exceeded
        401 Unauthorized: Not authenticated
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Create chunked upload session",
        description=(
            "Initialize a new chunked upload session. Returns session ID, "
            "chunk size, and total parts needed. Client uploads chunks to the "
            "provided targets."
        ),
        request=ChunkedUploadInitSerializer,
        responses={
            201: OpenApiResponse(
                response=ChunkedUploadSessionSerializer,
                description="Session created successfully",
            ),
            400: OpenApiResponse(description="Validation error or quota exceeded"),
            401: OpenApiResponse(description="Not authenticated"),
        },
        tags=["Chunked Upload"],
    )
    def post(self, request):
        """Create a new chunked upload session."""
        serializer = ChunkedUploadInitSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = get_chunked_upload_service()
        result = service.create_session(
            user=request.user,
            filename=serializer.validated_data["filename"],
            file_size=serializer.validated_data["file_size"],
            mime_type=serializer.validated_data["mime_type"],
            media_type=serializer.validated_data["media_type"],
        )

        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = ChunkedUploadSessionSerializer(result.data)
        return Response(
            output_serializer.data,
            status=status.HTTP_201_CREATED,
        )


class ChunkedUploadSessionDetailView(APIView):
    """
    Get or delete a chunked upload session.

    GET /api/v1/media/chunked/sessions/{session_id}/
        Get session status and progress.

    DELETE /api/v1/media/chunked/sessions/{session_id}/
        Abort the upload and clean up.

    Authentication:
        Requires valid JWT token.
        Only the session owner can access or abort.
    """

    permission_classes = [IsAuthenticated]

    def get_session(self, session_id, user):
        """Get session if it belongs to user."""
        try:
            return UploadSession.objects.get(pk=session_id, uploader=user)
        except UploadSession.DoesNotExist:
            return None

    @extend_schema(
        summary="Get upload session status",
        description="Get the current status and progress of an upload session.",
        responses={
            200: OpenApiResponse(
                response=ChunkedUploadSessionSerializer,
                description="Session status",
            ),
            404: OpenApiResponse(description="Session not found"),
        },
        tags=["Chunked Upload"],
    )
    def get(self, request, session_id):
        """Get session status."""
        session = self.get_session(session_id, request.user)
        if not session:
            return Response(
                {"error": "Upload session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ChunkedUploadSessionSerializer(session)
        return Response(serializer.data)

    @extend_schema(
        summary="Abort upload session",
        description="Abort an in-progress upload and clean up resources.",
        responses={
            204: OpenApiResponse(description="Session aborted"),
            404: OpenApiResponse(description="Session not found"),
            409: OpenApiResponse(description="Session already completed"),
        },
        tags=["Chunked Upload"],
    )
    def delete(self, request, session_id):
        """Abort the upload session."""
        session = self.get_session(session_id, request.user)
        if not session:
            return Response(
                {"error": "Upload session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        service = get_chunked_upload_service()
        result = service.abort_upload(session)

        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class ChunkedUploadPartTargetView(APIView):
    """
    Get upload target for a specific chunk.

    GET /api/v1/media/chunked/sessions/{session_id}/parts/{part_number}/target/
        Get the URL and method to upload a specific chunk.

    For local storage: Returns our server endpoint (direct=False)
    For S3 storage: Returns presigned S3 URL (direct=True)

    Authentication:
        Requires valid JWT token.
        Only the session owner can get targets.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get chunk upload target",
        description=(
            "Get the upload target for a specific chunk. For local storage, "
            "returns our server endpoint. For S3, returns a presigned URL."
        ),
        responses={
            200: OpenApiResponse(
                response=ChunkTargetSerializer,
                description="Upload target information",
            ),
            400: OpenApiResponse(description="Invalid part number"),
            404: OpenApiResponse(description="Session not found"),
            410: OpenApiResponse(description="Session expired"),
        },
        tags=["Chunked Upload"],
    )
    def get(self, request, session_id, part_number):
        """Get upload target for a chunk."""
        try:
            session = UploadSession.objects.get(pk=session_id, uploader=request.user)
        except UploadSession.DoesNotExist:
            return Response(
                {"error": "Upload session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        service = get_chunked_upload_service()
        result = service.get_chunk_target(session, part_number)

        if not result.success:
            if "expired" in result.error.lower():
                return Response(
                    {"error": result.error},
                    status=status.HTTP_410_GONE,
                )
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ChunkTargetSerializer(result.data)
        return Response(serializer.data)


class ChunkedUploadPartView(APIView):
    """
    Upload a chunk (local backend only).

    PUT /api/v1/media/chunked/sessions/{session_id}/parts/{part_number}/
        Upload raw binary chunk data.

    This endpoint receives chunks for local storage. For S3, clients
    upload directly to the presigned URL.

    Authentication:
        Requires valid JWT token.
        Only the session owner can upload.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Upload chunk",
        description="Upload a chunk to the server (local storage only).",
        responses={
            200: OpenApiResponse(
                response=PartCompletionResultSerializer,
                description="Chunk uploaded, progress updated",
            ),
            400: OpenApiResponse(description="Invalid chunk"),
            404: OpenApiResponse(description="Session not found"),
            409: OpenApiResponse(description="Invalid session status"),
        },
        tags=["Chunked Upload"],
    )
    def put(self, request, session_id, part_number):
        """Upload a chunk."""
        try:
            session = UploadSession.objects.get(pk=session_id, uploader=request.user)
        except UploadSession.DoesNotExist:
            return Response(
                {"error": "Upload session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Read raw binary body
        chunk_data = request.body

        if not chunk_data:
            return Response(
                {"error": "No chunk data provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = get_chunked_upload_service()
        result = service.receive_chunk(session, part_number, chunk_data)

        if not result.success:
            if "status" in result.error.lower():
                return Response(
                    {"error": result.error},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PartCompletionResultSerializer(result.data)
        return Response(serializer.data)


class ChunkedUploadPartCompleteView(APIView):
    """
    Record part completion (S3 backend).

    POST /api/v1/media/chunked/sessions/{session_id}/parts/{part_number}/complete/
        Record that a part was uploaded to S3.

    For S3 uploads, clients upload directly to S3, then call this
    endpoint to record the completion.

    Authentication:
        Requires valid JWT token.
        Only the session owner can record completions.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Record part completion",
        description="Record that a part was uploaded to S3 (S3 storage only).",
        responses={
            200: OpenApiResponse(
                response=PartCompletionResultSerializer,
                description="Part recorded, progress updated",
            ),
            400: OpenApiResponse(description="Invalid data"),
            404: OpenApiResponse(description="Session not found"),
        },
        tags=["Chunked Upload"],
    )
    def post(self, request, session_id, part_number):
        """Record part completion."""
        from media.serializers import ChunkedUploadPartCompleteSerializer

        try:
            session = UploadSession.objects.get(pk=session_id, uploader=request.user)
        except UploadSession.DoesNotExist:
            return Response(
                {"error": "Upload session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ChunkedUploadPartCompleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = get_chunked_upload_service()
        result = service.record_completed_part(
            session=session,
            part_number=part_number,
            etag=serializer.validated_data["etag"],
            size=serializer.validated_data["part_size"],
        )

        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = PartCompletionResultSerializer(result.data)
        return Response(output_serializer.data)


class ChunkedUploadFinalizeView(APIView):
    """
    Finalize the upload and create MediaFile.

    POST /api/v1/media/chunked/sessions/{session_id}/finalize/
        Complete the upload, assemble chunks, create MediaFile.

    This endpoint:
    - Verifies all parts are present
    - Assembles chunks into final file
    - Creates MediaFile record
    - Updates storage quota
    - Triggers scan/process pipeline
    - Cleans up temporary resources

    Authentication:
        Requires valid JWT token.
        Only the session owner can finalize.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Finalize upload",
        description=(
            "Complete the upload and create the MediaFile. "
            "Assembles chunks, updates quota, triggers processing."
        ),
        responses={
            201: OpenApiResponse(
                response=ChunkedUploadFinalizeResultSerializer,
                description="Upload complete, MediaFile created",
            ),
            400: OpenApiResponse(description="Missing parts or invalid state"),
            404: OpenApiResponse(description="Session not found"),
        },
        tags=["Chunked Upload"],
    )
    def post(self, request, session_id):
        """Finalize the upload."""
        try:
            session = UploadSession.objects.get(pk=session_id, uploader=request.user)
        except UploadSession.DoesNotExist:
            return Response(
                {"error": "Upload session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        service = get_chunked_upload_service()
        result = service.finalize_upload(session)

        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        media_file = result.data
        output = {
            "media_file_id": str(media_file.id),
            "message": "Upload complete. File is being processed.",
        }
        serializer = ChunkedUploadFinalizeResultSerializer(output)

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
        )


class ChunkedUploadProgressView(APIView):
    """
    Get upload progress.

    GET /api/v1/media/chunked/sessions/{session_id}/progress/
        Get detailed progress information.

    Authentication:
        Requires valid JWT token.
        Only the session owner can view progress.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get upload progress",
        description="Get detailed progress information for an upload session.",
        responses={
            200: OpenApiResponse(
                response=ChunkedUploadProgressSerializer,
                description="Progress information",
            ),
            404: OpenApiResponse(description="Session not found"),
        },
        tags=["Chunked Upload"],
    )
    def get(self, request, session_id):
        """Get progress information."""
        try:
            session = UploadSession.objects.get(pk=session_id, uploader=request.user)
        except UploadSession.DoesNotExist:
            return Response(
                {"error": "Upload session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        service = get_chunked_upload_service()
        progress = service.get_session_progress(session)
        serializer = ChunkedUploadProgressSerializer(progress)
        return Response(serializer.data)


# =============================================================================
# Quota Status View
# =============================================================================


class QuotaStatusView(APIView):
    """
    Get storage quota status for the current user.

    GET /api/v1/media/quota/
        Returns current storage usage and quota information.

    Authentication:
        Requires valid JWT token.

    Response:
        - total_storage_bytes: Bytes currently used
        - storage_quota_bytes: Maximum allowed bytes
        - storage_remaining_bytes: Bytes available (0 if over quota)
        - storage_used_percent: Percentage of quota used
        - storage_used_mb: Storage used in megabytes
        - storage_quota_mb: Quota in megabytes
        - can_upload: Boolean indicating if uploads are allowed
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="get_quota_status",
        summary="Get storage quota status",
        description="Returns the current user's storage quota status including "
        "used space, remaining space, and upload availability.",
        responses={
            200: OpenApiResponse(
                response=QuotaStatusSerializer,
                description="Quota status information",
            ),
        },
        tags=["Media"],
    )
    def get(self, request):
        """Get storage quota status for the current user."""
        profile = request.user.profile

        data = {
            "total_storage_bytes": profile.total_storage_bytes,
            "storage_quota_bytes": profile.storage_quota_bytes,
            "storage_remaining_bytes": profile.storage_remaining_bytes,
            "storage_used_percent": round(profile.storage_used_percent, 2),
            "storage_used_mb": profile.storage_used_mb,
            "storage_quota_mb": profile.storage_quota_mb,
            "can_upload": profile.storage_remaining_bytes > 0,
        }

        serializer = QuotaStatusSerializer(data)
        return Response(serializer.data)
