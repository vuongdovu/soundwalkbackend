"""
Model mixins providing reusable functionality for Django models.

This module contains abstract mixin classes that can be combined with
BaseModel to add specific functionality. Following the skeleton project
rules, these are generic infrastructure classes with no domain-specific logic.

Available Mixins:
    UUIDPrimaryKeyMixin: Use UUID as primary key
    SoftDeleteMixin: Soft delete support (is_deleted, deleted_at)
    SlugMixin: Auto-generated URL slugs
    OrderableMixin: User-defined ordering (position field)
    MetadataMixin: Flexible JSON metadata storage

Usage:
    from core.models import BaseModel
    from core.model_mixins import SoftDeleteMixin, UUIDPrimaryKeyMixin

    # Model with UUID primary key and soft delete
    class Document(UUIDPrimaryKeyMixin, SoftDeleteMixin, BaseModel):
        name = models.CharField(max_length=100)

    # Combine multiple mixins
    class Product(SlugMixin, MetadataMixin, BaseModel):
        name = models.CharField(max_length=100)

        def get_slug_source(self):
            return self.name

Note:
    - Always list mixins before BaseModel in inheritance
    - Mixins are abstract and don't create database tables
    - SoftDeleteMixin requires SoftDeleteManager (see core.managers)
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from typing import Any


class UUIDPrimaryKeyMixin(models.Model):
    """
    Use UUID as primary key instead of auto-increment integer.

    Benefits:
        - Non-guessable IDs (security through obscurity)
        - Safe for distributed systems (no ID collisions)
        - Can be generated client-side before database insert
        - URLs don't reveal record count or order

    Fields:
        id: UUIDField as primary key (auto-generated)

    Usage:
        class Document(UUIDPrimaryKeyMixin, BaseModel):
            name = models.CharField(max_length=100)

        # ID is automatically generated
        doc = Document.objects.create(name="Report")
        print(doc.id)  # UUID like: 550e8400-e29b-41d4-a716-446655440000

        # Can also provide your own UUID
        doc = Document.objects.create(
            id=uuid.uuid4(),
            name="Report"
        )

    Note:
        UUIDs are larger than integers (16 bytes vs 4-8 bytes) and
        have slightly slower index performance. Use when the benefits
        outweigh the costs.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique identifier for this record",
    )

    class Meta:
        abstract = True


class SoftDeleteMixin(models.Model):
    """
    Soft delete support for models.

    Instead of permanently deleting records, marks them as deleted.
    Deleted records can be restored and are preserved for auditing.

    Fields:
        is_deleted: Boolean flag indicating soft delete status
        deleted_at: Timestamp when the record was soft deleted

    Usage:
        from core.managers import SoftDeleteManager

        class Article(SoftDeleteMixin, BaseModel):
            objects = SoftDeleteManager()  # Excludes deleted by default
            all_objects = models.Manager()  # For admin access

            title = models.CharField(max_length=200)

        # Normal queries exclude deleted
        Article.objects.all()  # Only active articles

        # Soft delete
        article.soft_delete()

        # Restore
        article.restore()

        # Access deleted records
        Article.objects.deleted()  # Only deleted
        Article.all_objects.all()  # All including deleted

    Note:
        - Requires SoftDeleteManager as default manager
        - Add all_objects = models.Manager() for admin access
        - See core.managers for manager implementation
    """

    is_deleted = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether this record has been soft deleted",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when this record was soft deleted",
    )

    class Meta:
        abstract = True

    def soft_delete(self) -> None:
        """
        Mark this record as deleted.

        Sets is_deleted=True and deleted_at to current time.
        Does not actually remove the record from database.

        Example:
            article.soft_delete()
            assert article.is_deleted == True
        """
        # TODO: Implement
        # from django.utils import timezone
        # self.is_deleted = True
        # self.deleted_at = timezone.now()
        # self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        pass

    def restore(self) -> None:
        """
        Restore a soft-deleted record.

        Sets is_deleted=False and deleted_at to None.

        Example:
            article.restore()
            assert article.is_deleted == False
        """
        # TODO: Implement
        # self.is_deleted = False
        # self.deleted_at = None
        # self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        pass

    def hard_delete(self) -> None:
        """
        Permanently delete this record.

        Actually removes the record from database. Use with caution.

        Example:
            article.hard_delete()
            # Record is permanently gone

        Warning:
            This cannot be undone. Consider soft_delete() instead.
        """
        # TODO: Implement
        # super().delete()
        pass


class SlugMixin(models.Model):
    """
    Add URL-safe slug field with auto-generation support.

    Slugs are URL-safe identifiers typically derived from a title or name.
    Example: "My Article Title" -> "my-article-title"

    Fields:
        slug: URL-safe identifier, unique per model

    Usage:
        class Article(SlugMixin, BaseModel):
            title = models.CharField(max_length=200)

            def get_slug_source(self) -> str:
                return self.title

        # Slug auto-generated on save
        article = Article.objects.create(title="My Article")
        print(article.slug)  # "my-article"

        # Duplicate titles get numbered slugs
        article2 = Article.objects.create(title="My Article")
        print(article2.slug)  # "my-article-1"

    Override:
        get_slug_source(): Return the string to slugify (required)

    Note:
        If slug is provided explicitly, it won't be auto-generated.
    """

    slug = models.SlugField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="URL-safe identifier for this record",
    )

    class Meta:
        abstract = True

    def get_slug_source(self) -> str:
        """
        Return the value to slugify.

        Override in subclass to specify which field to use.

        Returns:
            String to convert to slug

        Example:
            def get_slug_source(self):
                return self.title
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement get_slug_source()"
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Auto-generate slug if not provided.

        Generates unique slug from get_slug_source() if slug is empty.
        Appends numbers for uniqueness if needed.
        """
        # TODO: Implement
        # if not self.slug:
        #     from django.utils.text import slugify
        #     base_slug = slugify(self.get_slug_source())
        #     slug = base_slug
        #     counter = 1
        #     model = self.__class__
        #
        #     # Handle uniqueness
        #     while model.objects.filter(slug=slug).exclude(pk=self.pk).exists():
        #         slug = f"{base_slug}-{counter}"
        #         counter += 1
        #
        #     self.slug = slug
        #
        super().save(*args, **kwargs)


class OrderableMixin(models.Model):
    """
    Support for user-defined ordering of records.

    Allows records to be manually ordered via a position field.
    Useful for sortable lists, menus, playlists, etc.

    Fields:
        position: Integer position for ordering (0-indexed)

    Usage:
        class MenuItem(OrderableMixin, BaseModel):
            name = models.CharField(max_length=100)

        # Create items (position defaults to 0)
        item1 = MenuItem.objects.create(name="Home", position=0)
        item2 = MenuItem.objects.create(name="About", position=1)
        item3 = MenuItem.objects.create(name="Contact", position=2)

        # Reorder
        item3.move_to(0)  # Move to first position
        item1.move_up()    # Move up one position
        item2.move_down()  # Move down one position

    Note:
        Default ordering is by position ascending (lowest first).
        Consider adding a scope field if ordering is per-parent
        (e.g., menu items per menu).
    """

    position = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="Position for ordering (lower numbers appear first)",
    )

    class Meta:
        abstract = True
        ordering = ["position"]

    def move_to(self, position: int) -> None:
        """
        Move to a specific position.

        Shifts other items as needed to maintain order.

        Args:
            position: Target position (0-indexed)

        Example:
            item.move_to(0)  # Move to first position
            item.move_to(5)  # Move to position 5
        """
        # TODO: Implement
        # old_position = self.position
        # if old_position == position:
        #     return
        #
        # model = self.__class__
        # if old_position < position:
        #     # Moving down: shift items up
        #     model.objects.filter(
        #         position__gt=old_position,
        #         position__lte=position
        #     ).update(position=models.F("position") - 1)
        # else:
        #     # Moving up: shift items down
        #     model.objects.filter(
        #         position__gte=position,
        #         position__lt=old_position
        #     ).update(position=models.F("position") + 1)
        #
        # self.position = position
        # self.save(update_fields=["position", "updated_at"])
        pass

    def move_up(self) -> None:
        """
        Move up one position (lower number).

        Does nothing if already at position 0.

        Example:
            item.move_up()  # Move from position 3 to position 2
        """
        # TODO: Implement
        # if self.position > 0:
        #     self.move_to(self.position - 1)
        pass

    def move_down(self) -> None:
        """
        Move down one position (higher number).

        Example:
            item.move_down()  # Move from position 2 to position 3
        """
        # TODO: Implement
        # self.move_to(self.position + 1)
        pass

    def move_to_top(self) -> None:
        """
        Move to first position (position 0).

        Example:
            item.move_to_top()  # Move to position 0
        """
        # TODO: Implement
        # self.move_to(0)
        pass

    def move_to_bottom(self) -> None:
        """
        Move to last position.

        Example:
            item.move_to_bottom()  # Move to last position
        """
        # TODO: Implement
        # model = self.__class__
        # max_position = model.objects.aggregate(
        #     max_pos=models.Max("position")
        # )["max_pos"] or 0
        # self.move_to(max_position)
        pass


class MetadataMixin(models.Model):
    """
    Flexible JSON metadata storage.

    Provides a JSONField for storing arbitrary key-value data.
    Useful for extensible attributes without schema changes.

    Fields:
        metadata: JSONField for arbitrary key-value data

    Usage:
        class Product(MetadataMixin, BaseModel):
            name = models.CharField(max_length=100)

        # Store arbitrary data
        product = Product.objects.create(name="Widget")
        product.set_meta("color", "red")
        product.set_meta("dimensions", {"width": 10, "height": 20})

        # Retrieve data
        color = product.get_meta("color")  # "red"
        size = product.get_meta("size", default="medium")  # "medium"

        # Check existence
        if product.has_meta("color"):
            ...

        # Remove data
        product.delete_meta("color")

    Note:
        metadata is a dict, so you can also access directly:
        product.metadata["color"] = "red"
        But use the helper methods for automatic saving.
    """

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Flexible key-value metadata storage",
    )

    class Meta:
        abstract = True

    def get_meta(self, key: str, default: Any = None) -> Any:
        """
        Get metadata value by key.

        Args:
            key: Metadata key
            default: Value to return if key not found

        Returns:
            Metadata value or default

        Example:
            color = product.get_meta("color", default="unknown")
        """
        return self.metadata.get(key, default)

    def set_meta(self, key: str, value: Any, save: bool = True) -> None:
        """
        Set metadata value and optionally save.

        Args:
            key: Metadata key
            value: Value to store (must be JSON-serializable)
            save: Whether to save the model (default True)

        Example:
            product.set_meta("color", "red")
            product.set_meta("temp_flag", True, save=False)  # Don't save yet
        """
        # TODO: Implement
        # self.metadata[key] = value
        # if save:
        #     self.save(update_fields=["metadata", "updated_at"])
        pass

    def delete_meta(self, key: str, save: bool = True) -> None:
        """
        Remove metadata key.

        Args:
            key: Metadata key to remove
            save: Whether to save the model (default True)

        Example:
            product.delete_meta("temporary_data")
        """
        # TODO: Implement
        # if key in self.metadata:
        #     del self.metadata[key]
        #     if save:
        #         self.save(update_fields=["metadata", "updated_at"])
        pass

    def has_meta(self, key: str) -> bool:
        """
        Check if metadata key exists.

        Args:
            key: Metadata key to check

        Returns:
            True if key exists in metadata

        Example:
            if product.has_meta("color"):
                ...
        """
        return key in self.metadata

    def clear_meta(self, save: bool = True) -> None:
        """
        Remove all metadata.

        Args:
            save: Whether to save the model (default True)

        Example:
            product.clear_meta()
        """
        # TODO: Implement
        # self.metadata = {}
        # if save:
        #     self.save(update_fields=["metadata", "updated_at"])
        pass
