"""Django admin configuration for media app."""

from django.contrib import admin

from media.models import MediaFile


@admin.register(MediaFile)
class MediaFileAdmin(admin.ModelAdmin):
    """Admin configuration for MediaFile model."""

    list_display = [
        "id",
        "original_filename",
        "media_type",
        "file_size",
        "uploader",
        "visibility",
        "processing_status",
        "scan_status",
        "created_at",
    ]
    list_filter = [
        "media_type",
        "visibility",
        "processing_status",
        "scan_status",
        "is_deleted",
    ]
    search_fields = ["original_filename", "uploader__email"]
    readonly_fields = [
        "id",
        "file_size",
        "mime_type",
        "created_at",
        "updated_at",
        "processing_started_at",
        "processing_completed_at",
        "scanned_at",
    ]
    raw_id_fields = ["uploader", "version_group"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
