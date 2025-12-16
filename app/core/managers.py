"""
Custom QuerySet and Manager classes for common patterns.

This module provides reusable manager patterns:
- SoftDeleteManager/QuerySet: Filter soft-deleted records
- BaseQuerySet: Common utility methods for querysets

Manager vs QuerySet:
    - QuerySet: Defines chainable methods (filter, exclude, etc.)
    - Manager: Attaches QuerySet to model, defines table-level operations

Usage:
    from core.managers import SoftDeleteManager, BaseQuerySet

    class Article(SoftDeleteMixin, BaseModel):
        objects = SoftDeleteManager()  # Default: excludes deleted
        all_objects = models.Manager()  # Includes deleted

        title = models.CharField(max_length=200)

    # Queries automatically exclude deleted
    Article.objects.all()  # Only active articles
    Article.objects.filter(title__contains="Django")

    # Include deleted when needed
    Article.all_objects.all()  # All articles including deleted

    # Explicit filtering
    Article.objects.deleted()  # Only deleted articles
    Article.objects.active()   # Same as default .all()

Related:
    - core.models.SoftDeleteMixin: Model mixin for soft delete fields
    - utils.mixins.SoftDeleteMixin: DRF viewset mixin for soft delete
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from datetime import date, datetime


class SoftDeleteQuerySet(models.QuerySet):
    """
    QuerySet that provides soft delete operations.

    By default, excludes soft-deleted records from queries.
    Provides methods for soft delete, restore, and explicit filtering.

    Methods:
        delete(): Soft delete (marks is_deleted=True)
        hard_delete(): Permanent delete
        restore(): Restore soft-deleted records
        deleted(): Filter to only deleted records
        active(): Filter to only active records

    Usage:
        # These exclude deleted by default (when using SoftDeleteManager)
        Article.objects.all()
        Article.objects.filter(author=user)

        # Soft delete a queryset
        Article.objects.filter(created_at__lt=cutoff).delete()

        # Permanently delete
        Article.objects.filter(created_at__lt=cutoff).hard_delete()

        # Restore deleted records
        Article.objects.deleted().filter(author=user).restore()

    Note:
        The default filtering of deleted records happens in SoftDeleteManager,
        not in this QuerySet. This allows all_objects to use the same QuerySet
        without filtering.
    """

    def delete(self) -> tuple[int, dict[str, int]]:
        """
        Soft delete all objects in queryset.

        Marks all matching records as deleted without removing from database.
        Sets is_deleted=True and deleted_at=now().

        Calls the on_soft_delete() hook on each instance for quota tracking
        and other cleanup operations.

        Returns:
            Tuple of (count, {model_name: count}) matching Django's delete()

        Example:
            # Soft delete old drafts
            count, _ = Article.objects.filter(
                status="draft",
                created_at__lt=one_year_ago
            ).delete()
            print(f"Soft deleted {count} articles")
        """
        from django.utils import timezone

        # Get instances to call hooks on (before marking as deleted)
        # Only process instances that aren't already deleted
        instances = list(self.filter(is_deleted=False))

        # Call on_soft_delete hook for each instance
        for instance in instances:
            if hasattr(instance, "on_soft_delete"):
                instance.on_soft_delete()

        # Perform the soft delete via update
        now = timezone.now()
        count = self.filter(is_deleted=False).update(is_deleted=True, deleted_at=now)

        return count, {self.model._meta.label: count}

    def hard_delete(self) -> tuple[int, dict[str, int]]:
        """
        Permanently delete all objects in queryset.

        Actually removes records from database. Use with caution.

        Returns:
            Tuple of (count, {model_name: count}) matching Django's delete()

        Example:
            # Permanently delete very old soft-deleted records
            Article.all_objects.filter(
                is_deleted=True,
                deleted_at__lt=two_years_ago
            ).hard_delete()

        Warning:
            This cannot be undone. Consider soft_delete() first.
        """
        return super().delete()

    def restore(self) -> int:
        """
        Restore all soft-deleted objects in queryset.

        Sets is_deleted=False and deleted_at=None.

        Calls the on_restore() hook on each instance for quota tracking
        and other operations.

        Returns:
            Number of restored records

        Example:
            # Restore accidentally deleted articles
            count = Article.objects.deleted().filter(
                deleted_at__gte=yesterday
            ).restore()
            print(f"Restored {count} articles")
        """
        # Get instances to call hooks on (before restoring)
        # Only process instances that are currently deleted
        instances = list(self.filter(is_deleted=True))

        # Call on_restore hook for each instance
        for instance in instances:
            if hasattr(instance, "on_restore"):
                instance.on_restore()

        # Perform the restore via update
        count = self.filter(is_deleted=True).update(is_deleted=False, deleted_at=None)

        return count

    def deleted(self) -> SoftDeleteQuerySet:
        """
        Filter to only soft-deleted records.

        Returns:
            QuerySet containing only deleted records

        Example:
            # List deleted articles for admin review
            deleted_articles = Article.objects.deleted()
        """
        return self.filter(is_deleted=True)

    def active(self) -> SoftDeleteQuerySet:
        """
        Filter to only active (non-deleted) records.

        This is the default behavior when using SoftDeleteManager.
        Explicit call is useful after modifying the queryset.

        Returns:
            QuerySet containing only active records

        Example:
            # Ensure we only see active records
            articles = Article.all_objects.filter(author=user).active()
        """
        return self.filter(is_deleted=False)


class SoftDeleteManager(models.Manager):
    """
    Manager that filters out soft-deleted records by default.

    Use as the default manager on models with SoftDeleteMixin.
    Always pair with a standard Manager for accessing deleted records.

    Usage:
        class Article(SoftDeleteMixin, BaseModel):
            objects = SoftDeleteManager()  # Default, excludes deleted
            all_objects = models.Manager()  # For admin access to deleted

            title = models.CharField(max_length=200)

        # Normal queries exclude deleted
        Article.objects.all()

        # Admin can see deleted
        Article.all_objects.all()

    Note:
        The filtering happens in get_queryset(), so all queries through
        this manager automatically exclude deleted records.
    """

    def get_queryset(self) -> SoftDeleteQuerySet:
        """
        Return queryset excluding soft-deleted records.

        Returns:
            SoftDeleteQuerySet filtered to is_deleted=False
        """
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def deleted(self) -> SoftDeleteQuerySet:
        """
        Shortcut to get only deleted records.

        Returns:
            QuerySet of soft-deleted records

        Example:
            Article.objects.deleted()  # Only deleted articles
        """
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=True)

    def with_deleted(self) -> SoftDeleteQuerySet:
        """
        Get queryset including deleted records.

        Alternative to using all_objects manager.

        Returns:
            QuerySet including all records

        Example:
            Article.objects.with_deleted().filter(author=user)
        """
        return SoftDeleteQuerySet(self.model, using=self._db)


class BaseQuerySet(models.QuerySet):
    """
    Enhanced QuerySet with common utility methods.

    Provides chainable methods for common query patterns.
    Use as base for custom querysets or attach to models via BaseManager.

    Methods:
        created_between(start, end): Filter by creation date range
        created_today(): Filter records created today
        created_this_week(): Filter records created this week
        updated_since(date): Filter records updated after date
        random(count): Return random records

    Usage:
        class ArticleQuerySet(BaseQuerySet):
            def published(self):
                return self.filter(status="published")

        class ArticleManager(models.Manager):
            def get_queryset(self):
                return ArticleQuerySet(self.model, using=self._db)

        class Article(BaseModel):
            objects = ArticleManager()

        # Use inherited methods
        Article.objects.created_today()
        Article.objects.created_between(start, end).published()

    Note:
        All methods assume the model has created_at and updated_at fields
        (provided by BaseModel).
    """

    def created_between(
        self,
        start: datetime | date,
        end: datetime | date,
    ) -> BaseQuerySet:
        """
        Filter records created within date range.

        Args:
            start: Start date/datetime (inclusive)
            end: End date/datetime (inclusive)

        Returns:
            Filtered queryset

        Example:
            # Articles created in January
            Article.objects.created_between(
                date(2024, 1, 1),
                date(2024, 1, 31)
            )
        """
        # TODO: Implement
        # return self.filter(created_at__gte=start, created_at__lte=end)
        return self.none()  # type: ignore

    def created_today(self) -> BaseQuerySet:
        """
        Filter records created today.

        Returns:
            QuerySet of records created today (UTC)

        Example:
            # Today's new users
            User.objects.created_today()
        """
        # TODO: Implement
        # from django.utils import timezone
        # today = timezone.now().date()
        # return self.filter(created_at__date=today)
        return self.none()  # type: ignore

    def created_this_week(self) -> BaseQuerySet:
        """
        Filter records created in the current week.

        Week starts on Monday.

        Returns:
            QuerySet of records created this week

        Example:
            # This week's orders
            Order.objects.created_this_week()
        """
        # TODO: Implement
        # from datetime import timedelta
        # from django.utils import timezone
        # today = timezone.now().date()
        # start_of_week = today - timedelta(days=today.weekday())
        # return self.filter(created_at__date__gte=start_of_week)
        return self.none()  # type: ignore

    def updated_since(self, since: datetime | date) -> BaseQuerySet:
        """
        Filter records updated after a given date.

        Useful for sync operations and change tracking.

        Args:
            since: Date/datetime to filter from

        Returns:
            QuerySet of records updated after the date

        Example:
            # Changes since last sync
            Product.objects.updated_since(last_sync_time)
        """
        # TODO: Implement
        # return self.filter(updated_at__gt=since)
        return self.none()  # type: ignore

    def random(self, count: int = 1) -> BaseQuerySet:
        """
        Return random records from queryset.

        Args:
            count: Number of random records to return

        Returns:
            QuerySet with random ordering, limited to count

        Example:
            # Random featured articles
            Article.objects.filter(featured=True).random(3)

        Warning:
            Uses database ORDER BY RANDOM() which can be slow on large tables.
            Consider caching or alternative strategies for high-traffic use.
        """
        # TODO: Implement
        # return self.order_by("?")[:count]
        return self.none()  # type: ignore

    def oldest(self) -> BaseQuerySet:
        """
        Order by creation date ascending (oldest first).

        Returns:
            QuerySet ordered oldest to newest

        Example:
            # Process oldest unprocessed items first
            Item.objects.filter(processed=False).oldest()
        """
        # TODO: Implement
        # return self.order_by("created_at")
        return self  # type: ignore

    def newest(self) -> BaseQuerySet:
        """
        Order by creation date descending (newest first).

        This is typically the default for BaseModel, but explicit
        call is clearer and works after other ordering.

        Returns:
            QuerySet ordered newest to oldest

        Example:
            # Latest comments
            Comment.objects.filter(post=post).newest()[:10]
        """
        # TODO: Implement
        # return self.order_by("-created_at")
        return self  # type: ignore


class BaseManager(models.Manager):
    """
    Manager that uses BaseQuerySet.

    Provides all BaseQuerySet methods at the manager level.

    Usage:
        class Article(BaseModel):
            objects = BaseManager()

        # Use BaseQuerySet methods
        Article.objects.created_today()
        Article.objects.random(5)
    """

    def get_queryset(self) -> BaseQuerySet:
        """Return BaseQuerySet for this manager."""
        return BaseQuerySet(self.model, using=self._db)
