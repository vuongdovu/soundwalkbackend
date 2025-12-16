"""Django admin configuration for media app."""

from django.contrib import admin

from media.models import MediaFile, MediaFileTag, Tag


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


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    """Admin configuration for Tag model."""

    list_display = [
        "id",
        "name",
        "slug",
        "tag_type",
        "category",
        "owner",
        "created_at",
    ]
    list_filter = ["tag_type", "category"]
    search_fields = ["name", "slug", "owner__email"]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["owner"]
    ordering = ["name"]

    def get_queryset(self, request):
        """Show all tags including deleted ones in admin."""
        return Tag.objects.all()


@admin.register(MediaFileTag)
class MediaFileTagAdmin(admin.ModelAdmin):
    """Admin configuration for MediaFileTag model."""

    list_display = [
        "id",
        "media_file",
        "tag",
        "applied_by",
        "confidence",
        "created_at",
    ]
    list_filter = ["tag__tag_type"]
    search_fields = [
        "media_file__original_filename",
        "tag__name",
        "applied_by__email",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["media_file", "tag", "applied_by"]
    ordering = ["-created_at"]
