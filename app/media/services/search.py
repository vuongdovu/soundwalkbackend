"""
Search services for PostgreSQL full-text search on media files.

Provides:
- SearchVectorService: Vector computation using raw SQL for efficiency
- SearchQueryBuilder: Query-level access control with FTS support

Search Vector Weights:
- Weight A (highest): original_filename
- Weight B (medium): tag names
- Weight C (lowest): extracted text content
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from django.contrib.postgres.search import SearchQuery
from django.db import connection
from django.db.models import Q
from django.db.models.expressions import RawSQL
from django.utils import timezone

from core.services import BaseService
from media.models import MediaAsset, MediaFile, MediaFileShare

if TYPE_CHECKING:
    from authentication.models import User
    from django.db.models import QuerySet


logger = logging.getLogger(__name__)


class SearchVectorService(BaseService):
    """
    Service for computing and updating search vectors.

    Uses raw SQL for efficiency when updating vectors, as Django's
    SearchVector annotation has limitations with cross-table joins.

    Search Vector Weights:
    - A (highest): original_filename - filename matches are most relevant
    - B (medium): tag names - explicit categorization
    - C (lowest): extracted text - document content

    Usage:
        # Initial vector (filename only, fast)
        SearchVectorService.update_vector_filename_only(media_file)

        # After tag changes (filename + tags)
        SearchVectorService.update_vector_filename_and_tags(media_file)

        # After processing (filename + tags + content)
        SearchVectorService.update_vector(media_file, include_content=True)
    """

    MAX_CONTENT_LENGTH = 50000  # 50KB text truncation limit

    @classmethod
    def _preprocess_filename(cls, filename: str) -> str:
        """
        Preprocess filename for better full-text search tokenization.

        PostgreSQL's to_tsvector treats underscores and dots as word joiners,
        causing 'budget_report_2024.xlsx' to become a single token. This method
        replaces common separators with spaces for proper tokenization.

        Args:
            filename: Original filename.

        Returns:
            Filename with separators replaced by spaces.
        """
        if not filename:
            return ""
        # Replace underscores, hyphens, and ALL dots with spaces
        # This ensures each word is indexed separately, including the extension
        return re.sub(r"[_\-.]", " ", filename)

    @classmethod
    def update_vector_filename_only(cls, media_file: MediaFile) -> bool:
        """
        Update search vector with filename only.

        This is the fastest update, used immediately after file creation.

        Args:
            media_file: MediaFile instance to update.

        Returns:
            True if successful, False on error.
        """
        try:
            filename = cls._preprocess_filename(media_file.original_filename)

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE media_mediafile
                    SET search_vector = setweight(
                        to_tsvector('english', COALESCE(%s, '')),
                        'A'
                    )
                    WHERE id = %s
                    """,
                    [filename, str(media_file.id)],
                )

            logger.debug(f"Updated search vector (filename only) for {media_file.id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update search vector for {media_file.id}: {e}")
            return False

    @classmethod
    def update_vector_filename_and_tags(cls, media_file: MediaFile) -> bool:
        """
        Update search vector with filename and tag names.

        Used after tag add/remove operations. Does not include document
        content to keep the operation fast.

        Args:
            media_file: MediaFile instance to update.

        Returns:
            True if successful, False on error.
        """
        try:
            filename = cls._preprocess_filename(media_file.original_filename)
            tag_names = " ".join(media_file.tags.values_list("name", flat=True))

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE media_mediafile
                    SET search_vector =
                        setweight(to_tsvector('english', COALESCE(%s, '')), 'A') ||
                        setweight(to_tsvector('english', COALESCE(%s, '')), 'B')
                    WHERE id = %s
                    """,
                    [filename, tag_names, str(media_file.id)],
                )

            logger.debug(f"Updated search vector (filename + tags) for {media_file.id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update search vector for {media_file.id}: {e}")
            return False

    @classmethod
    def update_vector(
        cls,
        media_file: MediaFile,
        include_content: bool = False,
    ) -> bool:
        """
        Update search vector with all components.

        This is the full update including extracted text content for
        documents. Used after processing completes.

        Args:
            media_file: MediaFile instance to update.
            include_content: Whether to include extracted text content.

        Returns:
            True if successful, False on error.
        """
        try:
            filename = cls._preprocess_filename(media_file.original_filename)
            tag_names = " ".join(media_file.tags.values_list("name", flat=True))
            content = ""

            if include_content:
                content = cls._get_extracted_text(media_file)

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE media_mediafile
                    SET search_vector =
                        setweight(to_tsvector('english', COALESCE(%s, '')), 'A') ||
                        setweight(to_tsvector('english', COALESCE(%s, '')), 'B') ||
                        setweight(to_tsvector('english', COALESCE(%s, '')), 'C')
                    WHERE id = %s
                    """,
                    [filename, tag_names, content, str(media_file.id)],
                )

            logger.debug(
                f"Updated search vector (full, content={include_content}) "
                f"for {media_file.id}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to update search vector for {media_file.id}: {e}")
            return False

    @classmethod
    def _get_extracted_text(cls, media_file: MediaFile) -> str:
        """
        Get extracted text content from MediaAsset.

        Args:
            media_file: MediaFile instance.

        Returns:
            Extracted text content, truncated to MAX_CONTENT_LENGTH.
        """
        try:
            asset = media_file.assets.filter(
                asset_type=MediaAsset.AssetType.EXTRACTED_TEXT
            ).first()

            if not asset or not asset.file:
                return ""

            # Read and decode content
            asset.file.seek(0)
            content = asset.file.read()

            # Handle bytes or string
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")

            # Truncate to limit
            return content[: cls.MAX_CONTENT_LENGTH]

        except Exception as e:
            logger.warning(f"Failed to read extracted text for {media_file.id}: {e}")
            return ""


class SearchQueryBuilder(BaseService):
    """
    Builder for search queries with access control at the database level.

    Access control is enforced in the queryset itself, ensuring accurate
    pagination counts. Users can only see files they:
    - Own
    - Have active (non-expired) shares for
    - Can view as staff (internal visibility)

    Excluded from all results:
    - Soft-deleted files (is_deleted=True)
    - Infected files (scan_status='infected')
    - Non-current versions (is_current=False)

    Usage:
        # Get accessible files for user
        qs = SearchQueryBuilder.build_accessible_queryset(user)

        # Search with query
        qs = SearchQueryBuilder.search(user, query="budget report")

        # Search with filters
        qs = SearchQueryBuilder.search(
            user,
            query="budget",
            filters={"media_type": "document", "tags": ["finance"]},
        )
    """

    @classmethod
    def build_accessible_queryset(cls, user: "User") -> "QuerySet[MediaFile]":
        """
        Build a queryset of files accessible to the user.

        Access rules:
        - Owner: Full access to own files
        - Shared: Files with active (non-expired) share grants
        - Staff: Can see internal visibility files

        Excludes:
        - Soft-deleted files
        - Infected files
        - Non-current versions

        Args:
            user: The requesting user.

        Returns:
            QuerySet of accessible MediaFile instances.
        """
        now = timezone.now()

        # Get file IDs that have active shares for this user
        shared_file_ids = (
            MediaFileShare.objects.filter(
                shared_with=user,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .values_list("media_file_id", flat=True)
        )

        # Access conditions
        is_owner = Q(uploader=user)
        has_active_share = Q(version_group_id__in=shared_file_ids)

        access_q = is_owner | has_active_share

        # Staff can also see internal files
        if user.is_staff:
            access_q |= Q(visibility=MediaFile.Visibility.INTERNAL)

        # Build queryset with access control and exclusions
        return (
            MediaFile.objects.filter(access_q)
            .filter(
                is_deleted=False,
                is_current=True,
            )
            .exclude(scan_status=MediaFile.ScanStatus.INFECTED)
            .distinct()
        )

    @classmethod
    def search(
        cls,
        user: "User",
        query: str | None = None,
        filters: dict | None = None,
    ) -> "QuerySet[MediaFile]":
        """
        Search files with access control and optional text query.

        Without query: Returns files ordered by -created_at (browse mode)
        With query: Full-text search with relevance ranking

        Args:
            user: The requesting user.
            query: Optional search query string.
            filters: Optional dict of filters:
                - media_type: str - filter by media type
                - uploaded_after: date - inclusive lower bound
                - uploaded_before: date - inclusive upper bound
                - tags: list[str] - tag slugs (all must match)
                - uploader: UUID - filter by uploader

        Returns:
            QuerySet of matching MediaFile instances.
        """
        qs = cls.build_accessible_queryset(user)

        # Apply filters
        if filters:
            qs = cls._apply_filters(qs, filters)

        # Apply text search or default ordering
        query_text = (query or "").strip()
        if query_text:
            search_query = SearchQuery(
                query_text,
                search_type="websearch",
                config="english",
            )
            qs = qs.filter(search_vector=search_query)
            # Use RawSQL for proper weighted ranking - Django's SearchRank
            # doesn't preserve weights from SearchVectorField
            qs = qs.annotate(
                relevance_score=RawSQL(
                    "ts_rank(search_vector, websearch_to_tsquery('english', %s))",
                    (query_text,),
                )
            ).order_by("-relevance_score", "-created_at")
        else:
            # Browse mode - order by created_at, null relevance_score
            qs = qs.order_by("-created_at")

        return qs

    @classmethod
    def _apply_filters(
        cls,
        qs: "QuerySet[MediaFile]",
        filters: dict,
    ) -> "QuerySet[MediaFile]":
        """
        Apply filters to the queryset.

        Args:
            qs: Base queryset.
            filters: Filter dictionary.

        Returns:
            Filtered queryset.
        """
        if media_type := filters.get("media_type"):
            qs = qs.filter(media_type=media_type)

        if uploaded_after := filters.get("uploaded_after"):
            qs = qs.filter(created_at__date__gte=uploaded_after)

        if uploaded_before := filters.get("uploaded_before"):
            qs = qs.filter(created_at__date__lte=uploaded_before)

        if tags := filters.get("tags"):
            # All tags must match (AND logic)
            for tag_slug in tags:
                qs = qs.filter(file_tags__tag__slug=tag_slug)

        if uploader := filters.get("uploader"):
            qs = qs.filter(uploader_id=uploader)

        return qs
