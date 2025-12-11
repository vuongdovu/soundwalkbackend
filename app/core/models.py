"""
Core base model providing common functionality for all domain models.

This module contains the abstract base class that should be inherited by all
domain models in the application. Following the skeleton project rules,
this is a generic infrastructure class with no domain-specific logic.

Base Classes:
    BaseModel: Abstract model with timestamps (created_at, updated_at)

For mixins (UUIDPrimaryKeyMixin, SoftDeleteMixin, SlugMixin, etc.),
see core.model_mixins.

Usage:
    from core.models import BaseModel
    from core.model_mixins import SoftDeleteMixin, UUIDPrimaryKeyMixin

    # Basic model with timestamps
    class Article(BaseModel):
        title = models.CharField(max_length=200)

    # Model with UUID primary key and soft delete
    class Document(UUIDPrimaryKeyMixin, SoftDeleteMixin, BaseModel):
        name = models.CharField(max_length=100)

Note:
    - Always list mixins before BaseModel in inheritance
    - Mixins are in core.model_mixins
    - ViewSet mixins are in core.viewset_mixins
"""

from __future__ import annotations

from django.db import models


class BaseModel(models.Model):
    """
    Abstract base model providing common fields for all models.

    All domain models should inherit from this class to ensure consistent
    tracking of creation and modification timestamps.

    Fields:
        created_at: Automatically set when the object is first created
        updated_at: Automatically updated whenever the object is saved

    Usage:
        class MyModel(BaseModel):
            name = models.CharField(max_length=100)
            # created_at and updated_at are automatically included

    Note:
        This is an abstract model (Meta.abstract = True) so it doesn't
        create a database table. Fields are added to inheriting models.
    """

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,  # Index for efficient time-based queries
        help_text="Timestamp when this record was created",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this record was last modified",
    )

    class Meta:
        abstract = True
        # Default ordering by creation time (newest first)
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Default string representation using primary key."""
        return f"{self.__class__.__name__}(id={self.pk})"
