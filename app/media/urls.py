"""
URL configuration for media app.

Routes:
    POST /upload/ - Upload a new media file
    GET /files/{file_id}/ - Get file details
    GET /files/{file_id}/download/ - Download file
    GET /files/{file_id}/view/ - View file inline
    GET /files/{file_id}/shares/ - List file shares
    POST /files/{file_id}/shares/ - Create a share
    DELETE /files/{file_id}/shares/{user_id}/ - Revoke a share
    GET /shared-with-me/ - List files shared with current user

Chunked Upload Routes:
    POST /chunked/sessions/ - Create upload session
    GET /chunked/sessions/{id}/ - Get session status
    DELETE /chunked/sessions/{id}/ - Abort upload
    GET /chunked/sessions/{id}/parts/{num}/target/ - Get chunk upload target
    PUT /chunked/sessions/{id}/parts/{num}/ - Upload chunk (local)
    POST /chunked/sessions/{id}/parts/{num}/complete/ - Record completion (S3)
    POST /chunked/sessions/{id}/finalize/ - Complete upload
    GET /chunked/sessions/{id}/progress/ - Get progress
"""

from django.urls import path

from media.views import (
    ChunkedUploadFinalizeView,
    ChunkedUploadPartCompleteView,
    ChunkedUploadPartTargetView,
    ChunkedUploadPartView,
    ChunkedUploadProgressView,
    ChunkedUploadSessionDetailView,
    ChunkedUploadSessionView,
    MediaFileDetailView,
    MediaFileDownloadView,
    MediaFileShareDeleteView,
    MediaFileShareView,
    MediaFilesSharedWithMeView,
    MediaFileViewView,
    MediaUploadView,
    QuotaStatusView,
)

app_name = "media"

urlpatterns = [
    # Quota Status
    path("quota/", QuotaStatusView.as_view(), name="quota-status"),
    # Upload
    path("upload/", MediaUploadView.as_view(), name="upload"),
    # File access
    path("files/<uuid:file_id>/", MediaFileDetailView.as_view(), name="detail"),
    path(
        "files/<uuid:file_id>/download/",
        MediaFileDownloadView.as_view(),
        name="download",
    ),
    path("files/<uuid:file_id>/view/", MediaFileViewView.as_view(), name="view"),
    # Sharing
    path("files/<uuid:file_id>/shares/", MediaFileShareView.as_view(), name="shares"),
    path(
        "files/<uuid:file_id>/shares/<uuid:user_id>/",
        MediaFileShareDeleteView.as_view(),
        name="share-delete",
    ),
    path(
        "shared-with-me/", MediaFilesSharedWithMeView.as_view(), name="shared-with-me"
    ),
    # Chunked Upload
    path(
        "chunked/sessions/",
        ChunkedUploadSessionView.as_view(),
        name="chunked-session-create",
    ),
    path(
        "chunked/sessions/<uuid:session_id>/",
        ChunkedUploadSessionDetailView.as_view(),
        name="chunked-session-detail",
    ),
    path(
        "chunked/sessions/<uuid:session_id>/parts/<int:part_number>/target/",
        ChunkedUploadPartTargetView.as_view(),
        name="chunked-part-target",
    ),
    path(
        "chunked/sessions/<uuid:session_id>/parts/<int:part_number>/",
        ChunkedUploadPartView.as_view(),
        name="chunked-part-upload",
    ),
    path(
        "chunked/sessions/<uuid:session_id>/parts/<int:part_number>/complete/",
        ChunkedUploadPartCompleteView.as_view(),
        name="chunked-part-complete",
    ),
    path(
        "chunked/sessions/<uuid:session_id>/finalize/",
        ChunkedUploadFinalizeView.as_view(),
        name="chunked-finalize",
    ),
    path(
        "chunked/sessions/<uuid:session_id>/progress/",
        ChunkedUploadProgressView.as_view(),
        name="chunked-progress",
    ),
]
