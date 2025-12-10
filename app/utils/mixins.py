"""
DRF mixins for common viewset and serializer functionality.

This module provides mixins for:
- Timestamp handling in serializers
- Pagination helpers for viewsets
- Bulk operations (create, update, delete)
- Soft delete support

Usage:
    from utils.mixins import BulkActionMixin, SoftDeleteMixin

    class MyViewSet(BulkActionMixin, viewsets.ModelViewSet):
        ...
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TimestampMixin:
    """
    Add timestamp fields to serializer output.

    Adds created_at and updated_at as read-only fields
    to any serializer that uses this mixin.

    Usage:
        class MySerializer(TimestampMixin, serializers.ModelSerializer):
            class Meta:
                model = MyModel
                fields = ["name", "created_at", "updated_at"]
    """

    def get_field_names(self, declared_fields, info):
        """Add timestamp fields to the list of fields."""
        fields = super().get_field_names(declared_fields, info)
        # Ensure timestamp fields are included
        if hasattr(info.model, "created_at") and "created_at" not in fields:
            fields = list(fields) + ["created_at"]
        if hasattr(info.model, "updated_at") and "updated_at" not in fields:
            fields = list(fields) + ["updated_at"]
        return fields


class PaginationMixin:
    """
    Add pagination helpers to viewsets.

    Provides methods for custom pagination responses
    and count optimization.

    Usage:
        class MyViewSet(PaginationMixin, viewsets.ModelViewSet):
            ...
    """

    def get_paginated_response_data(self, data, request=None):
        """
        Get pagination data for custom responses.

        Returns dict with count, next, previous, and results.
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

    Usage:
        class MyViewSet(BulkActionMixin, viewsets.ModelViewSet):
            ...

    Endpoints:
        POST /resource/bulk_create/
        PUT /resource/bulk_update/
        DELETE /resource/bulk_delete/
    """

    # TODO: Uncomment when implementing
    # from rest_framework.decorators import action
    # from rest_framework.response import Response

    # @action(detail=False, methods=["post"])
    def bulk_create(self, request):
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

    def perform_bulk_create(self, serializer):
        """Hook for customizing bulk create behavior."""
        serializer.save()

    # @action(detail=False, methods=["put", "patch"])
    def bulk_update(self, request):
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
    def bulk_delete(self, request):
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

    def perform_bulk_destroy(self, queryset):
        """Hook for customizing bulk delete behavior."""
        queryset.delete()


class SoftDeleteMixin:
    """
    Add soft delete support to viewsets.

    Uses is_deleted and deleted_at fields instead of
    actually deleting records.

    Usage:
        class MyViewSet(SoftDeleteMixin, viewsets.ModelViewSet):
            ...

    Model requirements:
        - is_deleted: BooleanField
        - deleted_at: DateTimeField (nullable)
    """

    def get_queryset(self):
        """Filter out soft-deleted records by default."""
        queryset = super().get_queryset()

        # Check if model supports soft delete
        if hasattr(queryset.model, "is_deleted"):
            # Allow including deleted with ?include_deleted=true
            if self.request.query_params.get("include_deleted") == "true":
                return queryset
            return queryset.filter(is_deleted=False)

        return queryset

    def perform_destroy(self, instance):
        """Soft delete instead of actual delete."""
        if hasattr(instance, "is_deleted"):
            from django.utils import timezone

            instance.is_deleted = True
            instance.deleted_at = timezone.now()
            instance.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
            logger.info(f"Soft deleted {instance.__class__.__name__} {instance.pk}")
        else:
            # Fall back to hard delete if model doesn't support soft delete
            super().perform_destroy(instance)

    # TODO: Uncomment when implementing
    # @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
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
