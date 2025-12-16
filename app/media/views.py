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
from media.serializers import (
    MediaFileSerializer,
    MediaFileShareCreateSerializer,
    MediaFileShareSerializer,
    MediaFileUploadSerializer,
)
from media.services.access_control import AccessControlService, FileAccessLevel
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
