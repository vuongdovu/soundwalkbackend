"""
Notification preference resolution.

This module handles the hierarchical preference resolution:
Global -> Category -> Type -> Channel

The resolution returns a ResolvedPreferences dataclass indicating
which channels are enabled for a specific user/notification-type combination.

Design Decisions:
    - TTL-based caching (5 min) for preference lookups
    - Bulk resolution uses 3 queries regardless of user count
    - Cache invalidation is optional (TTL handles staleness)

Usage:
    from notifications.preferences import PreferenceResolver

    # Single user resolution
    prefs = PreferenceResolver.resolve(user, notification_type)
    if prefs.push_enabled:
        # Send push notification

    # Bulk resolution for multiple users
    prefs_map = PreferenceResolver.resolve_bulk(user_ids, notification_type)
    for user_id, prefs in prefs_map.items():
        if not prefs.blocked:
            # Send notification to user_id
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from django.core.cache import cache

if TYPE_CHECKING:
    from authentication.models import User

    from notifications.models import (
        NotificationType,
        UserCategoryPreference,
        UserGlobalPreference,
        UserNotificationPreference,
    )

logger = logging.getLogger(__name__)


# Cache configuration
PREFERENCE_CACHE_TTL = 300  # 5 minutes
PREFERENCE_CACHE_PREFIX = "notif_pref"


@dataclass(frozen=True)
class ResolvedPreferences:
    """
    Resolved notification preferences for a user/type combination.

    Attributes:
        push_enabled: Whether push notifications are enabled
        email_enabled: Whether email notifications are enabled
        websocket_enabled: Whether websocket notifications are enabled
        blocked: True if all channels are blocked (any level disabled)
        blocked_reason: If blocked, the reason why (for SkipReason)
    """

    push_enabled: bool
    email_enabled: bool
    websocket_enabled: bool
    blocked: bool = False
    blocked_reason: str | None = None

    @property
    def any_enabled(self) -> bool:
        """True if at least one channel is enabled."""
        return (
            self.push_enabled or self.email_enabled or self.websocket_enabled
        ) and not self.blocked

    def is_channel_enabled(self, channel: str) -> bool:
        """Check if a specific channel is enabled."""
        if self.blocked:
            return False
        return getattr(self, f"{channel}_enabled", False)


class PreferenceResolver:
    """
    Resolves notification preferences using the hierarchy:
    Global -> Category -> Type -> Channel

    Uses TTL-based caching to reduce database queries.
    """

    @staticmethod
    def _get_cache_key(user_id: UUID | int, notification_type_id: int) -> str:
        """Build cache key for preferences."""
        return f"{PREFERENCE_CACHE_PREFIX}:{user_id}:{notification_type_id}"

    @classmethod
    def resolve(
        cls,
        user: User,
        notification_type: NotificationType,
        use_cache: bool = True,
    ) -> ResolvedPreferences:
        """
        Resolve preferences for a single user/type combination.

        Args:
            user: The user to resolve preferences for
            notification_type: The notification type
            use_cache: Whether to use cache (default True)

        Returns:
            ResolvedPreferences with enabled channels
        """
        # Check cache first
        if use_cache:
            cache_key = cls._get_cache_key(user.id, notification_type.id)
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        # Resolve from database
        resolved = cls._resolve_from_db(user, notification_type)

        # Cache result
        if use_cache:
            cache_key = cls._get_cache_key(user.id, notification_type.id)
            cache.set(cache_key, resolved, timeout=PREFERENCE_CACHE_TTL)

        return resolved

    @classmethod
    def _resolve_from_db(
        cls,
        user: User,
        notification_type: NotificationType,
    ) -> ResolvedPreferences:
        """
        Resolve preferences from database without caching.

        Hierarchy:
        1. Global disabled -> block all
        2. Category disabled -> block all
        3. Type disabled -> block all
        4. Per-channel: user override if set, else type default
        """
        from notifications.models import (
            SkipReason,
            UserCategoryPreference,
            UserGlobalPreference,
            UserNotificationPreference,
        )

        # 1. Check global preference
        try:
            global_pref = UserGlobalPreference.objects.get(user=user)
            if global_pref.all_disabled:
                return ResolvedPreferences(
                    push_enabled=False,
                    email_enabled=False,
                    websocket_enabled=False,
                    blocked=True,
                    blocked_reason=SkipReason.GLOBAL_DISABLED,
                )
        except UserGlobalPreference.DoesNotExist:
            pass  # No global pref = enabled by default

        # 2. Check category preference
        try:
            category_pref = UserCategoryPreference.objects.get(
                user=user,
                category=notification_type.category,
            )
            if category_pref.disabled:
                return ResolvedPreferences(
                    push_enabled=False,
                    email_enabled=False,
                    websocket_enabled=False,
                    blocked=True,
                    blocked_reason=SkipReason.CATEGORY_DISABLED,
                )
        except UserCategoryPreference.DoesNotExist:
            pass  # No category pref = enabled by default

        # 3. Check type preference
        type_pref = None
        try:
            type_pref = UserNotificationPreference.objects.get(
                user=user,
                notification_type=notification_type,
            )
            if type_pref.disabled:
                return ResolvedPreferences(
                    push_enabled=False,
                    email_enabled=False,
                    websocket_enabled=False,
                    blocked=True,
                    blocked_reason=SkipReason.TYPE_DISABLED,
                )
        except UserNotificationPreference.DoesNotExist:
            pass  # No type pref = use defaults

        # 4. Resolve each channel
        def resolve_channel(
            user_override: bool | None,
            type_supports: bool,
        ) -> bool:
            """Resolve a single channel's enabled status."""
            # If type doesn't support the channel, it's disabled
            if not type_supports:
                return False
            # If user has explicit preference, use it
            if user_override is not None:
                return user_override
            # Otherwise, channel is enabled if type supports it
            return True

        push_enabled = resolve_channel(
            type_pref.push_enabled if type_pref else None,
            notification_type.supports_push,
        )
        email_enabled = resolve_channel(
            type_pref.email_enabled if type_pref else None,
            notification_type.supports_email,
        )
        websocket_enabled = resolve_channel(
            type_pref.websocket_enabled if type_pref else None,
            notification_type.supports_websocket,
        )

        return ResolvedPreferences(
            push_enabled=push_enabled,
            email_enabled=email_enabled,
            websocket_enabled=websocket_enabled,
        )

    @classmethod
    def invalidate_cache(
        cls,
        user_id: UUID | int,
        notification_type_id: int | None = None,
    ) -> None:
        """
        Invalidate cached preferences for a user.

        Args:
            user_id: User whose cache to invalidate
            notification_type_id: Specific type to invalidate (optional)

        Note:
            Since we use TTL-based expiration, this is optional but useful
            for immediate preference changes.
        """
        if notification_type_id:
            cache_key = cls._get_cache_key(user_id, notification_type_id)
            cache.delete(cache_key)
        else:
            # For Redis, we could use pattern delete, but with TTL this is optional
            # The cache will naturally expire within PREFERENCE_CACHE_TTL seconds
            logger.debug(
                f"Cache invalidation requested for user {user_id}, relying on TTL"
            )

    @classmethod
    def resolve_bulk(
        cls,
        user_ids: list[UUID | int],
        notification_type: NotificationType,
    ) -> dict[UUID | int, ResolvedPreferences]:
        """
        Resolve preferences for multiple users efficiently.

        Uses 3 queries total regardless of user count:
        1. Fetch all global preferences
        2. Fetch all category preferences for this category
        3. Fetch all type preferences for this type

        Args:
            user_ids: List of user IDs
            notification_type: The notification type to resolve for

        Returns:
            Dict mapping user_id to ResolvedPreferences
        """
        from notifications.models import (
            UserCategoryPreference,
            UserGlobalPreference,
            UserNotificationPreference,
        )

        if not user_ids:
            return {}

        # Fetch all preferences in bulk (3 queries total)
        global_prefs = {
            pref.user_id: pref
            for pref in UserGlobalPreference.objects.filter(user_id__in=user_ids)
        }

        category_prefs = {
            pref.user_id: pref
            for pref in UserCategoryPreference.objects.filter(
                user_id__in=user_ids,
                category=notification_type.category,
            )
        }

        type_prefs = {
            pref.user_id: pref
            for pref in UserNotificationPreference.objects.filter(
                user_id__in=user_ids,
                notification_type=notification_type,
            )
        }

        # Resolve for each user
        results = {}
        for user_id in user_ids:
            results[user_id] = cls._resolve_for_user(
                user_id=user_id,
                notification_type=notification_type,
                global_pref=global_prefs.get(user_id),
                category_pref=category_prefs.get(user_id),
                type_pref=type_prefs.get(user_id),
            )

        return results

    @staticmethod
    def _resolve_for_user(
        user_id: UUID | int,
        notification_type: NotificationType,
        global_pref: UserGlobalPreference | None,
        category_pref: UserCategoryPreference | None,
        type_pref: UserNotificationPreference | None,
    ) -> ResolvedPreferences:
        """
        Resolve preferences for a single user with pre-fetched prefs.
        """
        from notifications.models import (
            SkipReason,
        )

        # 1. Global check
        if global_pref and global_pref.all_disabled:
            return ResolvedPreferences(
                push_enabled=False,
                email_enabled=False,
                websocket_enabled=False,
                blocked=True,
                blocked_reason=SkipReason.GLOBAL_DISABLED,
            )

        # 2. Category check
        if category_pref and category_pref.disabled:
            return ResolvedPreferences(
                push_enabled=False,
                email_enabled=False,
                websocket_enabled=False,
                blocked=True,
                blocked_reason=SkipReason.CATEGORY_DISABLED,
            )

        # 3. Type check
        if type_pref and type_pref.disabled:
            return ResolvedPreferences(
                push_enabled=False,
                email_enabled=False,
                websocket_enabled=False,
                blocked=True,
                blocked_reason=SkipReason.TYPE_DISABLED,
            )

        # 4. Channel resolution
        def resolve_channel(user_override: bool | None, type_supports: bool) -> bool:
            if not type_supports:
                return False
            if user_override is not None:
                return user_override
            return True

        return ResolvedPreferences(
            push_enabled=resolve_channel(
                type_pref.push_enabled if type_pref else None,
                notification_type.supports_push,
            ),
            email_enabled=resolve_channel(
                type_pref.email_enabled if type_pref else None,
                notification_type.supports_email,
            ),
            websocket_enabled=resolve_channel(
                type_pref.websocket_enabled if type_pref else None,
                notification_type.supports_websocket,
            ),
        )
