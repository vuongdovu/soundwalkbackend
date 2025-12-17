"""
Tests for media search functionality.

These tests verify:
- PostgreSQL full-text search with tsvector/tsquery
- Access control enforced at query level
- Search vector computation and updates
- API endpoint functionality
- Filter combinations
- Pagination with accurate counts

TDD: Write these tests first, then implement search functionality to pass them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from media.models import MediaAsset, MediaFile, MediaFileShare, Tag

if TYPE_CHECKING:
    from authentication.models import User
    from rest_framework.test import APIClient


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def media_file_factory(user: "User", db):
    """Factory function to create MediaFile instances for testing."""

    def create(
        filename: str = "test_file.jpg",
        media_type: str = MediaFile.MediaType.IMAGE,
        visibility: str = MediaFile.Visibility.PRIVATE,
        uploader: "User" = None,
        is_deleted: bool = False,
        scan_status: str = MediaFile.ScanStatus.CLEAN,
        processing_status: str = MediaFile.ProcessingStatus.READY,
        is_current: bool = True,
    ) -> MediaFile:
        target_user = uploader or user
        media_file = MediaFile.objects.create(
            file=f"test/{filename}",
            original_filename=filename,
            media_type=media_type,
            mime_type="image/jpeg" if media_type == "image" else "application/pdf",
            file_size=1024,
            uploader=target_user,
            visibility=visibility,
            scan_status=scan_status,
            processing_status=processing_status,
            is_current=is_current,
        )
        if is_deleted:
            # Bypass soft_delete hook for test setup
            MediaFile.all_objects.filter(pk=media_file.pk).update(
                is_deleted=True,
                deleted_at=timezone.now(),
            )
            media_file.refresh_from_db()
        return media_file

    return create


@pytest.fixture
def tag_factory(user: "User", db):
    """Factory function to create Tag instances for testing."""

    def create(
        name: str,
        owner: "User" = None,
        tag_type: str = Tag.TagType.USER,
    ) -> Tag:
        if tag_type == Tag.TagType.USER:
            tag, _ = Tag.get_or_create_user_tag(name=name, owner=owner or user)
        else:
            tag, _ = Tag.get_or_create_auto_tag(name=name)
        return tag

    return create


# =============================================================================
# Access Control Tests
# =============================================================================


@pytest.mark.django_db
class TestSearchAccessControl:
    """Tests for access control in search results."""

    def test_owner_can_search_own_files(
        self,
        user: "User",
        media_file_factory,
    ):
        """Owner's files should appear in their search results."""
        from media.services.search import SearchQueryBuilder

        media_file = media_file_factory(filename="my_document.pdf")

        queryset = SearchQueryBuilder.build_accessible_queryset(user)
        assert media_file in queryset

    def test_other_user_cannot_see_private_files(
        self,
        user: "User",
        other_user: "User",
        media_file_factory,
    ):
        """Private files should not appear in other users' search results."""
        from media.services.search import SearchQueryBuilder

        media_file = media_file_factory(
            filename="private_doc.pdf",
            visibility=MediaFile.Visibility.PRIVATE,
        )

        queryset = SearchQueryBuilder.build_accessible_queryset(other_user)
        assert media_file not in queryset

    def test_shared_files_appear_for_recipient(
        self,
        user: "User",
        other_user: "User",
        media_file_factory,
    ):
        """Files shared with user should appear in their search results."""
        from media.services.search import SearchQueryBuilder

        media_file = media_file_factory(
            filename="shared_doc.pdf",
            visibility=MediaFile.Visibility.SHARED,
        )
        MediaFileShare.objects.create(
            media_file=media_file.version_group,
            shared_by=user,
            shared_with=other_user,
            can_download=True,
        )

        queryset = SearchQueryBuilder.build_accessible_queryset(other_user)
        assert media_file in queryset

    def test_expired_shares_not_accessible(
        self,
        user: "User",
        other_user: "User",
        media_file_factory,
    ):
        """Files with expired shares should not appear in search results."""
        from media.services.search import SearchQueryBuilder

        media_file = media_file_factory(
            filename="expired_share.pdf",
            visibility=MediaFile.Visibility.SHARED,
        )
        MediaFileShare.objects.create(
            media_file=media_file.version_group,
            shared_by=user,
            shared_with=other_user,
            can_download=True,
            expires_at=timezone.now() - timedelta(hours=1),  # Expired
        )

        queryset = SearchQueryBuilder.build_accessible_queryset(other_user)
        assert media_file not in queryset

    def test_staff_can_see_internal_files(
        self,
        user: "User",
        staff_user: "User",
        media_file_factory,
    ):
        """Staff users should see internal visibility files."""
        from media.services.search import SearchQueryBuilder

        media_file = media_file_factory(
            filename="internal_doc.pdf",
            visibility=MediaFile.Visibility.INTERNAL,
        )

        queryset = SearchQueryBuilder.build_accessible_queryset(staff_user)
        assert media_file in queryset

    def test_non_staff_cannot_see_internal_files(
        self,
        user: "User",
        other_user: "User",
        media_file_factory,
    ):
        """Non-staff users should not see internal visibility files."""
        from media.services.search import SearchQueryBuilder

        media_file = media_file_factory(
            filename="internal_doc.pdf",
            visibility=MediaFile.Visibility.INTERNAL,
        )

        queryset = SearchQueryBuilder.build_accessible_queryset(other_user)
        assert media_file not in queryset

    def test_soft_deleted_files_excluded(
        self,
        user: "User",
        media_file_factory,
    ):
        """Soft-deleted files should not appear in search results."""
        from media.services.search import SearchQueryBuilder

        media_file = media_file_factory(
            filename="deleted_doc.pdf",
            is_deleted=True,
        )

        queryset = SearchQueryBuilder.build_accessible_queryset(user)
        assert media_file not in queryset

    def test_infected_files_excluded(
        self,
        user: "User",
        media_file_factory,
    ):
        """Infected files should not appear in search results."""
        from media.services.search import SearchQueryBuilder

        media_file = media_file_factory(
            filename="infected_doc.pdf",
            scan_status=MediaFile.ScanStatus.INFECTED,
        )

        queryset = SearchQueryBuilder.build_accessible_queryset(user)
        assert media_file not in queryset

    def test_only_current_versions_in_results(
        self,
        user: "User",
        media_file_factory,
    ):
        """Only current (is_current=True) versions should appear."""
        from media.services.search import SearchQueryBuilder

        # Create original file (current)
        current_file = media_file_factory(filename="document_current.pdf")

        # Create another file and make it a non-current version
        # by setting is_current=False (it has its own version_group)
        old_version = media_file_factory(
            filename="document_old.pdf",
            is_current=False,
        )

        queryset = SearchQueryBuilder.build_accessible_queryset(user)
        assert current_file in queryset
        assert old_version not in queryset


# =============================================================================
# Search Vector Tests
# =============================================================================


@pytest.mark.django_db
class TestSearchVectorComputation:
    """Tests for search vector computation."""

    def test_filename_included_in_vector(
        self,
        user: "User",
        media_file_factory,
    ):
        """Filename should be searchable via full-text search."""
        from media.services.search import SearchQueryBuilder, SearchVectorService

        media_file = media_file_factory(filename="budget_report_2024.xlsx")
        SearchVectorService.update_vector_filename_only(media_file)

        results = SearchQueryBuilder.search(user, query="budget")
        assert media_file in results

    def test_tag_names_included_in_vector(
        self,
        user: "User",
        media_file_factory,
        tag_factory,
    ):
        """Tag names should be searchable."""
        from media.services.search import SearchQueryBuilder, SearchVectorService

        media_file = media_file_factory(filename="photo.jpg")
        tag = tag_factory("vacation", owner=user)
        media_file.add_tag(tag, applied_by=user)

        SearchVectorService.update_vector_filename_and_tags(media_file)

        results = SearchQueryBuilder.search(user, query="vacation")
        assert media_file in results

    def test_extracted_text_included_in_vector(
        self,
        user: "User",
        media_file_factory,
    ):
        """Extracted text content should be searchable."""
        from media.services.search import SearchQueryBuilder, SearchVectorService

        media_file = media_file_factory(
            filename="contract.pdf",
            media_type=MediaFile.MediaType.DOCUMENT,
        )

        # Create extracted text asset
        text_content = b"This agreement is between Acme Corporation and XYZ Ltd."
        text_file = SimpleUploadedFile(
            "extracted.txt",
            text_content,
            content_type="text/plain",
        )
        MediaAsset.objects.create(
            media_file=media_file,
            asset_type=MediaAsset.AssetType.EXTRACTED_TEXT,
            file=text_file,
            file_size=len(text_content),
        )

        SearchVectorService.update_vector(media_file, include_content=True)

        results = SearchQueryBuilder.search(user, query="Acme Corporation")
        assert media_file in results

    def test_vector_without_tags_works(
        self,
        user: "User",
        media_file_factory,
    ):
        """Files without tags should still have working search vectors."""
        from media.services.search import SearchQueryBuilder, SearchVectorService

        media_file = media_file_factory(filename="report.pdf")
        SearchVectorService.update_vector_filename_and_tags(media_file)

        results = SearchQueryBuilder.search(user, query="report")
        assert media_file in results

    def test_special_characters_handled(
        self,
        user: "User",
        media_file_factory,
    ):
        """Special characters in filenames should be handled safely."""
        from media.services.search import SearchQueryBuilder, SearchVectorService

        media_file = media_file_factory(filename="file's_name (copy).pdf")
        SearchVectorService.update_vector_filename_only(media_file)

        # Should not raise and file should be findable
        results = SearchQueryBuilder.search(user, query="name")
        assert media_file in results

    def test_filename_weighted_higher_than_content(
        self,
        user: "User",
        media_file_factory,
    ):
        """Filename matches should rank higher than content matches."""
        from media.services.search import SearchQueryBuilder, SearchVectorService

        # File with "budget" in filename
        file_with_budget_name = media_file_factory(filename="budget_2024.xlsx")

        # File with "budget" in content only
        file_with_budget_content = media_file_factory(
            filename="report.pdf",
            media_type=MediaFile.MediaType.DOCUMENT,
        )
        text_content = b"This document contains budget information."
        text_file = SimpleUploadedFile(
            "extracted.txt",
            text_content,
            content_type="text/plain",
        )
        MediaAsset.objects.create(
            media_file=file_with_budget_content,
            asset_type=MediaAsset.AssetType.EXTRACTED_TEXT,
            file=text_file,
            file_size=len(text_content),
        )

        SearchVectorService.update_vector(file_with_budget_name, include_content=False)
        SearchVectorService.update_vector(
            file_with_budget_content, include_content=True
        )

        results = list(SearchQueryBuilder.search(user, query="budget"))

        # Filename match should rank first
        assert results[0] == file_with_budget_name


# =============================================================================
# Vector Update Tests
# =============================================================================


@pytest.mark.django_db
class TestSearchVectorUpdates:
    """Tests for search vector update triggers."""

    def test_vector_computed_on_creation(
        self,
        user: "User",
        media_file_factory,
    ):
        """Initial search vector should be computed on file creation."""
        from media.services.search import SearchVectorService

        media_file = media_file_factory(filename="new_document.pdf")
        SearchVectorService.update_vector_filename_only(media_file)

        media_file.refresh_from_db()
        assert media_file.search_vector is not None

    def test_vector_updated_on_tag_add(
        self,
        user: "User",
        media_file_factory,
        tag_factory,
    ):
        """Search vector should update when tag is added."""
        from media.services.search import SearchQueryBuilder, SearchVectorService

        media_file = media_file_factory(filename="photo.jpg")
        SearchVectorService.update_vector_filename_only(media_file)

        # Initially not searchable by tag
        results = SearchQueryBuilder.search(user, query="important")
        assert media_file not in results

        # Add tag
        tag = tag_factory("important", owner=user)
        media_file.add_tag(tag, applied_by=user)

        # Note: In production, signal handler would call this
        SearchVectorService.update_vector_filename_and_tags(media_file)

        # Now searchable by tag
        results = SearchQueryBuilder.search(user, query="important")
        assert media_file in results

    def test_vector_updated_on_tag_remove(
        self,
        user: "User",
        media_file_factory,
        tag_factory,
    ):
        """Search vector should update when tag is removed."""
        from media.services.search import SearchQueryBuilder, SearchVectorService

        media_file = media_file_factory(filename="photo.jpg")
        tag = tag_factory("removable", owner=user)
        media_file.add_tag(tag, applied_by=user)
        SearchVectorService.update_vector_filename_and_tags(media_file)

        # Searchable by tag
        results = SearchQueryBuilder.search(user, query="removable")
        assert media_file in results

        # Remove tag
        media_file.remove_tag(tag)

        # Note: In production, signal handler would call this
        SearchVectorService.update_vector_filename_and_tags(media_file)

        # No longer searchable by removed tag
        results = SearchQueryBuilder.search(user, query="removable")
        assert media_file not in results

    def test_failed_update_keeps_previous_vector(
        self,
        user: "User",
        media_file_factory,
    ):
        """If vector update fails, previous vector should remain."""
        from media.services.search import SearchVectorService

        media_file = media_file_factory(filename="stable.pdf")
        SearchVectorService.update_vector_filename_only(media_file)

        media_file.refresh_from_db()
        original_vector = media_file.search_vector

        # Simulate failed update (invalid file reference)
        with patch.object(
            SearchVectorService,
            "_get_extracted_text",
            side_effect=Exception("Read error"),
        ):
            # This should not raise
            SearchVectorService.update_vector(media_file, include_content=True)

        media_file.refresh_from_db()
        # Vector should remain unchanged
        assert media_file.search_vector == original_vector


# =============================================================================
# API Endpoint Tests
# =============================================================================


@pytest.mark.django_db
class TestSearchEndpoint:
    """Tests for the search API endpoint."""

    def test_empty_query_returns_browse_mode(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
    ):
        """Empty query should return files ordered by created_at (browse mode)."""
        from media.services.search import SearchVectorService

        file1 = media_file_factory(filename="first.pdf")
        file2 = media_file_factory(filename="second.pdf")
        SearchVectorService.update_vector_filename_only(file1)
        SearchVectorService.update_vector_filename_only(file2)

        url = reverse("media:media-search")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data
        # Most recent first
        assert response.data["results"][0]["id"] == str(file2.id)

    def test_single_character_query_works(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
    ):
        """Single character queries should work."""
        from media.services.search import SearchVectorService

        media_file = media_file_factory(filename="x_marks_spot.pdf")
        SearchVectorService.update_vector_filename_only(media_file)

        url = reverse("media:media-search")
        response = authenticated_client.get(url, {"q": "x"})

        assert response.status_code == status.HTTP_200_OK

    def test_whitespace_only_query_treated_as_browse(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
    ):
        """Whitespace-only query should be treated as browse mode."""
        from media.services.search import SearchVectorService

        media_file = media_file_factory(filename="document.pdf")
        SearchVectorService.update_vector_filename_only(media_file)

        url = reverse("media:media-search")
        response = authenticated_client.get(url, {"q": "   "})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) > 0

    def test_relevance_score_present_with_query(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
    ):
        """Results should include relevance_score when query is provided."""
        from media.services.search import SearchVectorService

        media_file = media_file_factory(filename="budget_report.pdf")
        SearchVectorService.update_vector_filename_only(media_file)

        url = reverse("media:media-search")
        response = authenticated_client.get(url, {"q": "budget"})

        assert response.status_code == status.HTTP_200_OK
        if response.data["results"]:
            assert "relevance_score" in response.data["results"][0]
            assert response.data["results"][0]["relevance_score"] is not None

    def test_relevance_score_null_without_query(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
    ):
        """Results should have null relevance_score in browse mode."""
        from media.services.search import SearchVectorService

        media_file = media_file_factory(filename="document.pdf")
        SearchVectorService.update_vector_filename_only(media_file)

        url = reverse("media:media-search")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        if response.data["results"]:
            assert response.data["results"][0]["relevance_score"] is None

    def test_thumbnail_url_nullable(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
    ):
        """Thumbnail URL should be null for files without thumbnails."""
        from media.services.search import SearchVectorService

        media_file = media_file_factory(filename="no_thumb.pdf")
        SearchVectorService.update_vector_filename_only(media_file)

        url = reverse("media:media-search")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        if response.data["results"]:
            assert "thumbnail_url" in response.data["results"][0]
            # Can be None if no thumbnail asset exists
            assert response.data["results"][0]["thumbnail_url"] is None

    def test_unauthenticated_returns_401(
        self,
        api_client: "APIClient",
    ):
        """Unauthenticated requests should return 401."""
        url = reverse("media:media-search")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# =============================================================================
# Filter Tests
# =============================================================================


@pytest.mark.django_db
class TestSearchFilters:
    """Tests for search filters."""

    def test_media_type_filter(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
    ):
        """Filter by media_type should only return matching files."""
        from media.services.search import SearchVectorService

        image = media_file_factory(
            filename="photo.jpg",
            media_type=MediaFile.MediaType.IMAGE,
        )
        document = media_file_factory(
            filename="report.pdf",
            media_type=MediaFile.MediaType.DOCUMENT,
        )
        SearchVectorService.update_vector_filename_only(image)
        SearchVectorService.update_vector_filename_only(document)

        url = reverse("media:media-search")
        response = authenticated_client.get(url, {"media_type": "document"})

        assert response.status_code == status.HTTP_200_OK
        result_ids = [r["id"] for r in response.data["results"]]
        assert str(document.id) in result_ids
        assert str(image.id) not in result_ids

    def test_date_range_filter_inclusive(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
    ):
        """Date range filters should be inclusive."""
        from media.services.search import SearchVectorService

        media_file = media_file_factory(filename="dated.pdf")
        SearchVectorService.update_vector_filename_only(media_file)

        today = timezone.now().date()
        url = reverse("media:media-search")
        response = authenticated_client.get(
            url,
            {
                "uploaded_after": today.isoformat(),
                "uploaded_before": today.isoformat(),
            },
        )

        assert response.status_code == status.HTTP_200_OK
        result_ids = [r["id"] for r in response.data["results"]]
        assert str(media_file.id) in result_ids

    def test_tags_filter_all_must_match(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
        tag_factory,
    ):
        """Tags filter requires ALL specified tags to match (AND logic)."""
        from media.services.search import SearchVectorService

        tag1 = tag_factory("urgent", owner=user)
        tag2 = tag_factory("finance", owner=user)

        # File with both tags
        file_both = media_file_factory(filename="both_tags.pdf")
        file_both.add_tag(tag1, applied_by=user)
        file_both.add_tag(tag2, applied_by=user)
        SearchVectorService.update_vector_filename_and_tags(file_both)

        # File with only one tag
        file_one = media_file_factory(filename="one_tag.pdf")
        file_one.add_tag(tag1, applied_by=user)
        SearchVectorService.update_vector_filename_and_tags(file_one)

        url = reverse("media:media-search")
        response = authenticated_client.get(url, {"tags": "urgent,finance"})

        assert response.status_code == status.HTTP_200_OK
        result_ids = [r["id"] for r in response.data["results"]]
        assert str(file_both.id) in result_ids
        assert str(file_one.id) not in result_ids

    def test_uploader_filter(
        self,
        authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_factory,
    ):
        """Uploader filter should only return files from specified user."""
        from media.services.search import SearchVectorService

        my_file = media_file_factory(filename="my_file.pdf", uploader=user)
        # Share other user's file so authenticated user can see it
        other_file = media_file_factory(
            filename="other_file.pdf",
            uploader=other_user,
            visibility=MediaFile.Visibility.SHARED,
        )
        MediaFileShare.objects.create(
            media_file=other_file.version_group,
            shared_by=other_user,
            shared_with=user,
            can_download=True,
        )
        SearchVectorService.update_vector_filename_only(my_file)
        SearchVectorService.update_vector_filename_only(other_file)

        url = reverse("media:media-search")
        response = authenticated_client.get(url, {"uploader": str(user.id)})

        assert response.status_code == status.HTTP_200_OK
        result_ids = [r["id"] for r in response.data["results"]]
        assert str(my_file.id) in result_ids
        assert str(other_file.id) not in result_ids

    def test_filters_combine_with_and(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
        tag_factory,
    ):
        """Multiple filters should combine with AND logic."""
        from media.services.search import SearchVectorService

        tag = tag_factory("work", owner=user)

        # Image with tag
        image_with_tag = media_file_factory(
            filename="photo_work.jpg",
            media_type=MediaFile.MediaType.IMAGE,
        )
        image_with_tag.add_tag(tag, applied_by=user)
        SearchVectorService.update_vector_filename_and_tags(image_with_tag)

        # Document with tag
        doc_with_tag = media_file_factory(
            filename="report_work.pdf",
            media_type=MediaFile.MediaType.DOCUMENT,
        )
        doc_with_tag.add_tag(tag, applied_by=user)
        SearchVectorService.update_vector_filename_and_tags(doc_with_tag)

        # Image without tag
        image_no_tag = media_file_factory(
            filename="personal.jpg",
            media_type=MediaFile.MediaType.IMAGE,
        )
        SearchVectorService.update_vector_filename_only(image_no_tag)

        url = reverse("media:media-search")
        response = authenticated_client.get(
            url,
            {"media_type": "image", "tags": "work"},
        )

        assert response.status_code == status.HTTP_200_OK
        result_ids = [r["id"] for r in response.data["results"]]
        assert str(image_with_tag.id) in result_ids
        assert str(doc_with_tag.id) not in result_ids
        assert str(image_no_tag.id) not in result_ids


# =============================================================================
# Pagination Tests
# =============================================================================


@pytest.mark.django_db
class TestSearchPagination:
    """Tests for search pagination."""

    def test_default_page_size(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_factory,
    ):
        """Default page size should be 20."""
        from media.services.search import SearchVectorService

        # Create 25 files
        for i in range(25):
            f = media_file_factory(filename=f"file_{i:02d}.pdf")
            SearchVectorService.update_vector_filename_only(f)

        url = reverse("media:media-search")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 20
        assert response.data["count"] == 25

    def test_accurate_counts_with_access_control(
        self,
        authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_factory,
    ):
        """
        Counts should be accurate with access control filtering.

        This is critical: counts must reflect what the user can see,
        not total files in the database.
        """
        from media.services.search import SearchVectorService

        # 5 files user owns
        for i in range(5):
            f = media_file_factory(filename=f"my_file_{i}.pdf", uploader=user)
            SearchVectorService.update_vector_filename_only(f)

        # 3 files other user owns (user can't see)
        for i in range(3):
            f = media_file_factory(
                filename=f"other_file_{i}.pdf",
                uploader=other_user,
            )
            SearchVectorService.update_vector_filename_only(f)

        url = reverse("media:media-search")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # Should only see own files
        assert response.data["count"] == 5


# =============================================================================
# Reconciliation Task Tests
# =============================================================================


@pytest.mark.django_db
class TestReconciliationTask:
    """Tests for the weekly reconciliation task."""

    def test_finds_documents_missing_content_index(
        self,
        user: "User",
        media_file_factory,
    ):
        """Reconciliation should find documents with extracted text but outdated vectors."""
        from media.tasks import reconcile_search_vectors
        from media.services.search import SearchVectorService

        # Create document with extracted text
        doc = media_file_factory(
            filename="old_document.pdf",
            media_type=MediaFile.MediaType.DOCUMENT,
        )

        # Add extracted text asset
        text_content = b"Important quarterly results."
        text_file = SimpleUploadedFile(
            "extracted.txt",
            text_content,
            content_type="text/plain",
        )
        MediaAsset.objects.create(
            media_file=doc,
            asset_type=MediaAsset.AssetType.EXTRACTED_TEXT,
            file=text_file,
            file_size=len(text_content),
        )

        # Set vector with filename only (simulating old data)
        SearchVectorService.update_vector_filename_only(doc)

        # Run reconciliation
        with patch.object(
            SearchVectorService,
            "update_vector",
        ) as mock_update:
            reconcile_search_vectors()
            # Should have been called for the document with extracted text
            assert mock_update.called

    def test_skips_non_documents(
        self,
        user: "User",
        media_file_factory,
    ):
        """Reconciliation should skip non-document files."""
        from media.tasks import reconcile_search_vectors
        from media.services.search import SearchVectorService

        # Create image (no content to index)
        image = media_file_factory(
            filename="photo.jpg",
            media_type=MediaFile.MediaType.IMAGE,
        )
        SearchVectorService.update_vector_filename_only(image)

        with patch.object(
            SearchVectorService,
            "update_vector",
        ) as mock_update:
            reconcile_search_vectors()
            # Should not update images
            for call in mock_update.call_args_list:
                assert call[0][0].media_type != MediaFile.MediaType.IMAGE

    def test_skips_files_without_extracted_text(
        self,
        user: "User",
        media_file_factory,
    ):
        """Reconciliation should skip documents without extracted text assets."""
        from media.tasks import reconcile_search_vectors
        from media.services.search import SearchVectorService

        # Create document without extracted text
        doc = media_file_factory(
            filename="no_text.pdf",
            media_type=MediaFile.MediaType.DOCUMENT,
        )
        SearchVectorService.update_vector_filename_only(doc)

        with patch.object(
            SearchVectorService,
            "update_vector",
        ) as mock_update:
            reconcile_search_vectors()
            # Should not update documents without extracted text
            for call in mock_update.call_args_list:
                file = call[0][0]
                if file.id == doc.id:
                    pytest.fail("Should not update doc without extracted text")


# =============================================================================
# Task Chain Tests
# =============================================================================


@pytest.mark.django_db
class TestSearchVectorTask:
    """Tests for the update_search_vector_safe Celery task."""

    def test_task_never_raises(
        self,
        user: "User",
        media_file_factory,
    ):
        """The task should never raise exceptions (to not break pipeline)."""
        from media.tasks import update_search_vector_safe

        # Test with valid file
        media_file = media_file_factory(filename="valid.pdf")
        result = update_search_vector_safe(
            {"status": "success", "media_file_id": str(media_file.id)}
        )
        assert "status" in result

        # Test with non-existent file
        result = update_search_vector_safe(
            {
                "status": "success",
                "media_file_id": "00000000-0000-0000-0000-000000000000",
            }
        )
        assert "status" in result

        # Test with invalid input
        result = update_search_vector_safe("invalid_input")
        assert "status" in result

    def test_task_returns_dict_for_chain(
        self,
        user: "User",
        media_file_factory,
    ):
        """Task should return dict to allow chain continuation."""
        from media.tasks import update_search_vector_safe

        media_file = media_file_factory(filename="chain_test.pdf")
        result = update_search_vector_safe(
            {"status": "success", "media_file_id": str(media_file.id)}
        )

        assert isinstance(result, dict)
        assert "media_file_id" in result

    def test_task_skips_processing_if_upstream_failed(
        self,
        user: "User",
        media_file_factory,
    ):
        """Task should gracefully handle upstream failures."""
        from media.tasks import update_search_vector_safe

        media_file = media_file_factory(filename="failed_scan.pdf")
        result = update_search_vector_safe(
            {
                "status": "failed",
                "media_file_id": str(media_file.id),
                "error": "Scan failed",
            }
        )

        assert result["status"] == "skipped"
