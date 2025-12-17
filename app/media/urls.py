"""
URL configuration for media app.

API Documentation Groups (following [App Name] - [Group Name] pattern):

Media - Upload:
    POST /upload/                                  - Upload media file

Media - Files:
    GET /files/{file_id}/                         - Get file details
    GET /files/{file_id}/download/                - Download file
    GET /files/{file_id}/view/                    - View file inline

Media - Search:
    GET /search/                                  - Search files with full-text search

Media - Sharing:
    GET /files/{file_id}/shares/                  - List file shares
    POST /files/{file_id}/shares/                 - Share file with user
    DELETE /files/{file_id}/shares/{user_id}/     - Revoke share
    GET /shared-with-me/                          - List files shared with me

Media - Tags:
    GET /tags/                                    - List all tags
    POST /tags/                                   - Create user tag
    GET /tags/{tag_id}/                           - Get tag details
    DELETE /tags/{tag_id}/                        - Delete user tag
    GET /files/{file_id}/tags/                    - List file tags
    POST /files/{file_id}/tags/                   - Apply tag to file
    DELETE /files/{file_id}/tags/{tag_id}/        - Remove tag from file
    GET /files/by-tags/                           - Query files by tags

Media - Chunked Upload:
    POST /chunked/sessions/                       - Create upload session
    GET /chunked/sessions/{id}/                   - Get session status
    DELETE /chunked/sessions/{id}/                - Abort session
    GET /chunked/sessions/{id}/parts/{num}/target/- Get chunk upload target
    PUT /chunked/sessions/{id}/parts/{num}/       - Upload chunk (local)
    POST /chunked/sessions/{id}/parts/{num}/complete/ - Record chunk completion (S3)
    POST /chunked/sessions/{id}/finalize/         - Finalize upload
    GET /chunked/sessions/{id}/progress/          - Get progress

Media - Quota:
    GET /quota/                                   - Get storage quota status
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
    FilesByTagView,
    MediaFileDetailView,
    MediaFileDownloadView,
    MediaFileSearchView,
    MediaFileShareDeleteView,
    MediaFileShareView,
    MediaFilesSharedWithMeView,
    MediaFileTagDeleteView,
    MediaFileTagsView,
    MediaFileViewView,
    MediaUploadView,
    QuotaStatusView,
    TagDetailView,
    TagListCreateView,
)

app_name = "media"

urlpatterns = [
    # Search
    path("search/", MediaFileSearchView.as_view(), name="media-search"),
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
    # Tags
    path("tags/", TagListCreateView.as_view(), name="tag-list"),
    path("tags/<uuid:tag_id>/", TagDetailView.as_view(), name="tag-detail"),
    path(
        "files/<uuid:file_id>/tags/",
        MediaFileTagsView.as_view(),
        name="file-tags",
    ),
    path(
        "files/<uuid:file_id>/tags/<uuid:tag_id>/",
        MediaFileTagDeleteView.as_view(),
        name="file-tag-delete",
    ),
    path("files/by-tags/", FilesByTagView.as_view(), name="files-by-tags"),
]
