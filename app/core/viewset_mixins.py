"""
ViewSet mixins for common DRF functionality.

This module provides generic, non-domain-specific mixins for viewsets:
- PaginationMixin: Pagination helpers for custom responses
- BulkActionMixin: Batch create/update/delete operations
- SoftDeleteViewSetMixin: Soft delete support at viewset level

These mixins complement the model-level patterns in core.models
and core.managers.

Usage:
    from core.viewset_mixins import (
        PaginationMixin,
        BulkActionMixin,
        SoftDeleteViewSetMixin,
    )
    from core.models import BaseModel
    from core.model_mixins import SoftDeleteMixin
    from core.managers import SoftDeleteManager

    # Model with soft delete support
    class Article(SoftDeleteMixin, BaseModel):
        objects = SoftDeleteManager()
        title = models.CharField(max_length=200)

    # ViewSet with pagination, soft delete, and bulk actions
    class ArticleViewSet(
        PaginationMixin,
        SoftDeleteViewSetMixin,
        BulkActionMixin,
        viewsets.ModelViewSet
    ):
        queryset = Article.objects.all()

Note:
    These are infrastructure patterns, not domain-specific utilities.
    For serializer mixins, see core.serializer_mixins.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


class PaginationMixin:
    """
    Add pagination helpers to viewsets.

    Provides methods for custom pagination responses
    and count optimization.

    Usage:
        from core.viewset_mixins import PaginationMixin

        class MyViewSet(PaginationMixin, viewsets.ModelViewSet):
            queryset = MyModel.objects.all()

            def list(self, request):
                queryset = self.filter_queryset(self.get_queryset())
                page = self.paginate_queryset(queryset)
                if page is not None:
                    serializer = self.get_serializer(page, many=True)
                    return self.get_paginated_response(serializer.data)
                serializer = self.get_serializer(queryset, many=True)
                return Response(serializer.data)

    Note:
        This is a generic helper for pagination responses.
        Works with any DRF pagination class.
    """

    def get_paginated_response_data(
        self, data: Any, _request: Any = None
    ) -> dict[str, Any]:
        """
        Get pagination data for custom responses.

        Useful for building custom response formats that include
        pagination metadata alongside the results.

        Args:
            data: Serialized data from the queryset
            _request: Optional request object (for future extensions)

        Returns:
            Dict with count, next, previous, and results

        Example:
            pagination_data = self.get_paginated_response_data(serializer.data)
            return Response({
                **pagination_data,
                "extra_metadata": "value"
            })
        """
        # TODO: Implement pagination helper
        # paginator = self.paginator
        # return {
        #     "count": paginator.page.paginator.count,
        #     "next": paginator.get_next_link(),
        #     "previous": paginator.get_previous_link(),
        #     "results": data,
        # }
        return {"results": data}


class BulkActionMixin:
    """
    Add bulk create/update/delete to viewsets.

    Provides actions for batch operations on multiple objects.
    This is a generic infrastructure pattern for CRUD operations.

    Usage:
        class MyViewSet(BulkActionMixin, viewsets.ModelViewSet):
            queryset = MyModel.objects.all()
            serializer_class = MySerializer

    Endpoints:
        POST /resource/bulk_create/
        PUT /resource/bulk_update/
        DELETE /resource/bulk_delete/

    Note:
        Methods are stubs with TODO implementations.
        Uncomment the @action decorators when implementing.
    """

    # TODO: Uncomment when implementing
    # from rest_framework.decorators import action
    # from rest_framework.response import Response

    # @action(detail=False, methods=["post"])
    def bulk_create(self, _request: Any) -> None:
        """
        Create multiple objects at once.

        Request body:
            {"items": [{"name": "Item 1"}, {"name": "Item 2"}]}

        Returns:
            List of created objects
        """
        # TODO: Implement bulk create
        # items = request.data.get("items", [])
        # serializer = self.get_serializer(data=items, many=True)
        # serializer.is_valid(raise_exception=True)
        # self.perform_bulk_create(serializer)
        # return Response(serializer.data, status=201)
        pass

    def perform_bulk_create(self, serializer: Any) -> None:
        """Hook for customizing bulk create behavior."""
        serializer.save()

    # @action(detail=False, methods=["put", "patch"])
    def bulk_update(self, _request: Any) -> None:
        """
        Update multiple objects at once.

        Request body:
            {"items": [{"id": 1, "name": "New Name"}, ...]}

        Returns:
            List of updated objects
        """
        # TODO: Implement bulk update
        # items = request.data.get("items", [])
        # instances = []
        # for item in items:
        #     instance = self.get_queryset().get(pk=item.get("id"))
        #     serializer = self.get_serializer(instance, data=item, partial=True)
        #     serializer.is_valid(raise_exception=True)
        #     instances.append(serializer.save())
        # return Response(self.get_serializer(instances, many=True).data)
        pass

    # @action(detail=False, methods=["delete"])
    def bulk_delete(self, _request: Any) -> None:
        """
        Delete multiple objects at once.

        Request body:
            {"ids": [1, 2, 3]}

        Returns:
            Deletion count
        """
        # TODO: Implement bulk delete
        # ids = request.data.get("ids", [])
        # queryset = self.get_queryset().filter(pk__in=ids)
        # count = queryset.count()
        # self.perform_bulk_destroy(queryset)
        # return Response({"deleted": count})
        pass

    def perform_bulk_destroy(self, queryset: Any) -> None:
        """Hook for customizing bulk delete behavior."""
        queryset.delete()


class SoftDeleteViewSetMixin:
    """
    Add soft delete support to viewsets.

    Uses is_deleted and deleted_at fields instead of
    actually deleting records. Works with models that use
    core.models.SoftDeleteMixin and core.managers.SoftDeleteManager.

    Usage:
        from core.models import SoftDeleteMixin, BaseModel
        from core.managers import SoftDeleteManager
        from core.viewset_mixins import SoftDeleteViewSetMixin

        # Model uses core mixin
        class Article(SoftDeleteMixin, BaseModel):
            objects = SoftDeleteManager()
            title = models.CharField(max_length=200)

        # ViewSet uses this mixin
        class ArticleViewSet(SoftDeleteViewSetMixin, viewsets.ModelViewSet):
            queryset = Article.objects.all()

    Query parameters:
        ?include_deleted=true - Include soft-deleted records in results

    Model requirements:
        - is_deleted: BooleanField
        - deleted_at: DateTimeField (nullable)
        - See core.models.SoftDeleteMixin for the model implementation
    """

    # Type hints for mixin - these are provided by the viewset
    request: Any

    def get_queryset(self) -> Any:
        """Filter out soft-deleted records by default."""
        queryset = super().get_queryset()  # type: ignore[misc]

        # Check if model supports soft delete
        if hasattr(queryset.model, "is_deleted"):
            # Allow including deleted with ?include_deleted=true
            if self.request.query_params.get("include_deleted") == "true":
                return queryset
            return queryset.filter(is_deleted=False)

        return queryset

    def perform_destroy(self, instance: Any) -> None:
        """Soft delete instead of actual delete."""
        if hasattr(instance, "is_deleted"):
            from django.utils import timezone

            instance.is_deleted = True
            instance.deleted_at = timezone.now()
            instance.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
            logger.info(f"Soft deleted {instance.__class__.__name__} {instance.pk}")
        else:
            # Fall back to hard delete if model doesn't support soft delete
            super().perform_destroy(instance)  # type: ignore[misc]

    # TODO: Uncomment when implementing
    # @action(detail=True, methods=["post"])
    def restore(self, _request: Any, _pk: Any = None) -> None:
        """
        Restore a soft-deleted record.

        Returns:
            Restored object
        """
        # TODO: Implement restore
        # instance = self.get_object()
        # if hasattr(instance, "is_deleted"):
        #     instance.is_deleted = False
        #     instance.deleted_at = None
        #     instance.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        #     logger.info(f"Restored {instance.__class__.__name__} {instance.pk}")
        # return Response(self.get_serializer(instance).data)
        pass
