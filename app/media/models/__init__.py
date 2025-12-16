"""
Media models package.

Exports:
    MediaFile: Primary model for user-uploaded media files
    MediaAsset: Generated assets (thumbnails, previews, etc.)
    MediaFileShare: Explicit sharing grants between users
    UploadSession: Tracks chunked/resumable uploads
    Tag: Tags for categorizing media files
    MediaFileTag: Through table for file-tag relationships
"""

from media.models.media_asset import MediaAsset
from media.models.media_file import MediaFile
from media.models.media_file_share import MediaFileShare
from media.models.media_file_tag import MediaFileTag
from media.models.tag import Tag
from media.models.upload_session import UploadSession

__all__ = [
    "MediaAsset",
    "MediaFile",
    "MediaFileShare",
    "MediaFileTag",
    "Tag",
    "UploadSession",
]
