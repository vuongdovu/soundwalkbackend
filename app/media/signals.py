"""
Django signals for the media app.

Provides handlers for:
- Search vector updates on tag changes
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db.models.signals import post_delete, post_save

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


def connect_signals():
    """
    Connect all signal handlers.

    Called from MediaConfig.ready() to ensure signals are connected
    after all models are loaded.
    """
    from media.models import MediaFileTag

    # Connect tag change handlers
    post_save.connect(
        update_search_vector_on_tag_add,
        sender=MediaFileTag,
        dispatch_uid="search_vector_tag_add",
    )
    post_delete.connect(
        update_search_vector_on_tag_remove,
        sender=MediaFileTag,
        dispatch_uid="search_vector_tag_remove",
    )

    logger.debug("Media signals connected")


def update_search_vector_on_tag_add(
    sender,
    instance,
    created: bool,
    **kwargs,
) -> None:
    """
    Update search vector when a tag is added to a file.

    Only triggers on creation (not updates) to avoid redundant work.

    Args:
        sender: MediaFileTag model class.
        instance: MediaFileTag instance.
        created: True if new record created.
        **kwargs: Additional signal arguments.
    """
    if not created:
        return

    try:
        from media.services.search import SearchVectorService

        media_file = instance.media_file
        SearchVectorService.update_vector_filename_and_tags(media_file)
        logger.debug(f"Search vector updated after tag add for {media_file.id}")

    except Exception as e:
        # Log but don't raise - tagging should succeed even if search update fails
        logger.error(f"Failed to update search vector on tag add: {e}")


def update_search_vector_on_tag_remove(
    sender,
    instance,
    **kwargs,
) -> None:
    """
    Update search vector when a tag is removed from a file.

    Args:
        sender: MediaFileTag model class.
        instance: MediaFileTag instance being deleted.
        **kwargs: Additional signal arguments.
    """
    try:
        from media.services.search import SearchVectorService

        media_file = instance.media_file
        SearchVectorService.update_vector_filename_and_tags(media_file)
        logger.debug(f"Search vector updated after tag remove for {media_file.id}")

    except Exception as e:
        # Log but don't raise - tag removal should succeed even if search update fails
        logger.error(f"Failed to update search vector on tag remove: {e}")
