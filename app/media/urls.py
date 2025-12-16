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
"""

from django.urls import path

from media.views import (
    MediaFileDetailView,
    MediaFileDownloadView,
    MediaFileShareDeleteView,
    MediaFileShareView,
    MediaFilesSharedWithMeView,
    MediaFileViewView,
    MediaUploadView,
)

app_name = "media"

urlpatterns = [
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
]
