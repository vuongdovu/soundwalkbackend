"""
Tests for notification preference resolution.

Tests cover:
- PreferenceResolver.resolve() with hierarchical preferences
- PreferenceResolver.resolve_bulk() for batch operations
- Cache behavior (hits, misses, invalidation)
- Edge cases (no preferences, partial preferences)

Usage:
    pytest app/notifications/tests/test_preferences.py -v
"""

import pytest
from django.core.cache import cache

from notifications.models import (
    NotificationCategory,
    NotificationType,
    SkipReason,
    UserCategoryPreference,
    UserGlobalPreference,
    UserNotificationPreference,
)
from notifications.preferences import (
    PREFERENCE_CACHE_PREFIX,
    PreferenceResolver,
    ResolvedPreferences,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def notification_type_social(db):
    """Create a social notification type with all channels enabled."""
    return NotificationType.objects.create(
        key="social_follow",
        display_name="New Follower",
        category=NotificationCategory.SOCIAL,
        title_template="{actor_name} followed you",
        body_template="You have a new follower!",
        is_active=True,
        supports_push=True,
        supports_email=True,
        supports_websocket=True,
    )


@pytest.fixture
def notification_type_transactional(db):
    """Create a transactional notification type."""
    return NotificationType.objects.create(
        key="order_shipped",
        display_name="Order Shipped",
        category=NotificationCategory.TRANSACTIONAL,
        title_template="Order {order_id} shipped",
        body_template="Your order is on the way!",
        is_active=True,
        supports_push=True,
        supports_email=True,
        supports_websocket=False,
    )


@pytest.fixture
def notification_type_push_only(db):
    """Create a notification type that only supports push."""
    return NotificationType.objects.create(
        key="push_reminder",
        display_name="Reminder",
        category=NotificationCategory.SYSTEM,
        title_template="Reminder",
        body_template="Don't forget!",
        is_active=True,
        supports_push=True,
        supports_email=False,
        supports_websocket=False,
    )


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


# =============================================================================
# ResolvedPreferences Tests
# =============================================================================


class TestResolvedPreferences:
    """Tests for ResolvedPreferences dataclass."""

    def test_any_enabled_true_when_push_enabled(self):
        """any_enabled returns True when push is enabled."""
        prefs = ResolvedPreferences(
            push_enabled=True,
            email_enabled=False,
            websocket_enabled=False,
        )
        assert prefs.any_enabled is True

    def test_any_enabled_true_when_email_enabled(self):
        """any_enabled returns True when email is enabled."""
        prefs = ResolvedPreferences(
            push_enabled=False,
            email_enabled=True,
            websocket_enabled=False,
        )
        assert prefs.any_enabled is True

    def test_any_enabled_true_when_websocket_enabled(self):
        """any_enabled returns True when websocket is enabled."""
        prefs = ResolvedPreferences(
            push_enabled=False,
            email_enabled=False,
            websocket_enabled=True,
        )
        assert prefs.any_enabled is True

    def test_any_enabled_false_when_all_disabled(self):
        """any_enabled returns False when all channels disabled."""
        prefs = ResolvedPreferences(
            push_enabled=False,
            email_enabled=False,
            websocket_enabled=False,
        )
        assert prefs.any_enabled is False

    def test_any_enabled_false_when_blocked(self):
        """any_enabled returns False when blocked (even if channels enabled)."""
        prefs = ResolvedPreferences(
            push_enabled=True,
            email_enabled=True,
            websocket_enabled=True,
            blocked=True,
            blocked_reason=SkipReason.GLOBAL_DISABLED,
        )
        assert prefs.any_enabled is False

    def test_is_channel_enabled_returns_true_for_enabled(self):
        """is_channel_enabled returns True for enabled channels."""
        prefs = ResolvedPreferences(
            push_enabled=True,
            email_enabled=False,
            websocket_enabled=True,
        )
        assert prefs.is_channel_enabled("push") is True
        assert prefs.is_channel_enabled("websocket") is True

    def test_is_channel_enabled_returns_false_for_disabled(self):
        """is_channel_enabled returns False for disabled channels."""
        prefs = ResolvedPreferences(
            push_enabled=True,
            email_enabled=False,
            websocket_enabled=True,
        )
        assert prefs.is_channel_enabled("email") is False

    def test_is_channel_enabled_returns_false_when_blocked(self):
        """is_channel_enabled returns False when blocked."""
        prefs = ResolvedPreferences(
            push_enabled=True,
            email_enabled=True,
            websocket_enabled=True,
            blocked=True,
        )
        assert prefs.is_channel_enabled("push") is False
        assert prefs.is_channel_enabled("email") is False

    def test_is_channel_enabled_returns_false_for_unknown(self):
        """is_channel_enabled returns False for unknown channels."""
        prefs = ResolvedPreferences(
            push_enabled=True,
            email_enabled=True,
            websocket_enabled=True,
        )
        assert prefs.is_channel_enabled("sms") is False


# =============================================================================
# PreferenceResolver.resolve() Tests
# =============================================================================


class TestPreferenceResolverResolve:
    """Tests for PreferenceResolver.resolve() method."""

    def test_default_preferences_when_no_prefs_set(
        self, user, notification_type_social
    ):
        """With no preferences, all type-supported channels are enabled."""
        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.push_enabled is True
        assert prefs.email_enabled is True
        assert prefs.websocket_enabled is True
        assert prefs.blocked is False
        assert prefs.blocked_reason is None

    def test_type_channel_support_respected(self, user, notification_type_push_only):
        """Channels not supported by type are disabled."""
        prefs = PreferenceResolver.resolve(user, notification_type_push_only)

        assert prefs.push_enabled is True
        assert prefs.email_enabled is False  # Type doesn't support
        assert prefs.websocket_enabled is False  # Type doesn't support

    def test_global_disabled_blocks_all(self, user, notification_type_social):
        """Global all_disabled=True blocks all channels."""
        UserGlobalPreference.objects.create(user=user, all_disabled=True)

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.blocked is True
        assert prefs.blocked_reason == SkipReason.GLOBAL_DISABLED
        assert prefs.push_enabled is False
        assert prefs.email_enabled is False
        assert prefs.websocket_enabled is False

    def test_global_enabled_does_not_block(self, user, notification_type_social):
        """Global all_disabled=False does not block."""
        UserGlobalPreference.objects.create(user=user, all_disabled=False)

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.blocked is False
        assert prefs.push_enabled is True

    def test_category_disabled_blocks_all(self, user, notification_type_social):
        """Category disabled blocks all channels for that category."""
        UserCategoryPreference.objects.create(
            user=user,
            category=NotificationCategory.SOCIAL,
            disabled=True,
        )

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.blocked is True
        assert prefs.blocked_reason == SkipReason.CATEGORY_DISABLED

    def test_category_enabled_does_not_block(self, user, notification_type_social):
        """Category disabled=False does not block."""
        UserCategoryPreference.objects.create(
            user=user,
            category=NotificationCategory.SOCIAL,
            disabled=False,
        )

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.blocked is False

    def test_different_category_not_affected(self, user, notification_type_social):
        """Disabling one category doesn't affect others."""
        # Disable transactional category
        UserCategoryPreference.objects.create(
            user=user,
            category=NotificationCategory.TRANSACTIONAL,
            disabled=True,
        )

        # Social type should not be affected
        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.blocked is False
        assert prefs.push_enabled is True

    def test_type_disabled_blocks_all(self, user, notification_type_social):
        """Type-level disabled blocks all channels."""
        UserNotificationPreference.objects.create(
            user=user,
            notification_type=notification_type_social,
            disabled=True,
        )

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.blocked is True
        assert prefs.blocked_reason == SkipReason.TYPE_DISABLED

    def test_type_channel_override_push_disabled(self, user, notification_type_social):
        """User can disable push for specific type."""
        UserNotificationPreference.objects.create(
            user=user,
            notification_type=notification_type_social,
            push_enabled=False,
        )

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.push_enabled is False
        assert prefs.email_enabled is True  # Not overridden
        assert prefs.websocket_enabled is True  # Not overridden

    def test_type_channel_override_email_enabled(self, user, notification_type_social):
        """User can explicitly enable email."""
        UserNotificationPreference.objects.create(
            user=user,
            notification_type=notification_type_social,
            email_enabled=True,
        )

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.email_enabled is True

    def test_type_channel_null_inherits_default(self, user, notification_type_social):
        """Null channel value inherits from type default."""
        UserNotificationPreference.objects.create(
            user=user,
            notification_type=notification_type_social,
            push_enabled=None,  # Inherit
            email_enabled=False,  # Explicit override
        )

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.push_enabled is True  # Inherited from type
        assert prefs.email_enabled is False  # Overridden

    def test_hierarchy_global_takes_precedence(self, user, notification_type_social):
        """Global disabled takes precedence over category/type."""
        # Set global disabled
        UserGlobalPreference.objects.create(user=user, all_disabled=True)

        # Try to enable at category and type level
        UserCategoryPreference.objects.create(
            user=user,
            category=NotificationCategory.SOCIAL,
            disabled=False,
        )
        UserNotificationPreference.objects.create(
            user=user,
            notification_type=notification_type_social,
            push_enabled=True,
        )

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.blocked is True
        assert prefs.blocked_reason == SkipReason.GLOBAL_DISABLED

    def test_hierarchy_category_takes_precedence_over_type(
        self, user, notification_type_social
    ):
        """Category disabled takes precedence over type preferences."""
        UserCategoryPreference.objects.create(
            user=user,
            category=NotificationCategory.SOCIAL,
            disabled=True,
        )
        UserNotificationPreference.objects.create(
            user=user,
            notification_type=notification_type_social,
            push_enabled=True,
        )

        prefs = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs.blocked is True
        assert prefs.blocked_reason == SkipReason.CATEGORY_DISABLED


# =============================================================================
# Cache Tests
# =============================================================================


class TestPreferenceResolverCache:
    """Tests for PreferenceResolver caching behavior."""

    def test_cache_hit_avoids_database(
        self, user, notification_type_social, django_assert_num_queries
    ):
        """Second resolve call uses cache, no database queries."""
        # First call - populates cache
        prefs1 = PreferenceResolver.resolve(user, notification_type_social)

        # Second call - should use cache
        with django_assert_num_queries(0):
            prefs2 = PreferenceResolver.resolve(user, notification_type_social)

        assert prefs1 == prefs2

    def test_use_cache_false_bypasses_cache(self, user, notification_type_social):
        """use_cache=False always queries database."""
        # First call with caching
        PreferenceResolver.resolve(user, notification_type_social)

        # Modify preferences in database
        UserGlobalPreference.objects.create(user=user, all_disabled=True)

        # Without cache bypass, would return stale data
        cached_prefs = PreferenceResolver.resolve(
            user, notification_type_social, use_cache=True
        )
        assert cached_prefs.blocked is False  # Stale

        # With cache bypass, gets fresh data
        fresh_prefs = PreferenceResolver.resolve(
            user, notification_type_social, use_cache=False
        )
        assert fresh_prefs.blocked is True  # Fresh

    def test_invalidate_cache_specific_type(
        self, user, notification_type_social, notification_type_transactional
    ):
        """invalidate_cache with type_id only invalidates that key."""
        # Populate cache for both types
        PreferenceResolver.resolve(user, notification_type_social)
        PreferenceResolver.resolve(user, notification_type_transactional)

        # Invalidate only social type
        PreferenceResolver.invalidate_cache(user.id, notification_type_social.id)

        # Check cache keys
        social_key = (
            f"{PREFERENCE_CACHE_PREFIX}:{user.id}:{notification_type_social.id}"
        )
        trans_key = (
            f"{PREFERENCE_CACHE_PREFIX}:{user.id}:{notification_type_transactional.id}"
        )

        assert cache.get(social_key) is None  # Invalidated
        assert cache.get(trans_key) is not None  # Still cached


# =============================================================================
# Bulk Resolution Tests
# =============================================================================


class TestPreferenceResolverBulk:
    """Tests for PreferenceResolver.resolve_bulk() method."""

    def test_bulk_resolve_empty_list(self, notification_type_social):
        """resolve_bulk with empty list returns empty dict."""
        result = PreferenceResolver.resolve_bulk([], notification_type_social)
        assert result == {}

    def test_bulk_resolve_single_user(self, user, notification_type_social):
        """resolve_bulk works with single user."""
        result = PreferenceResolver.resolve_bulk([user.id], notification_type_social)

        assert len(result) == 1
        assert user.id in result
        assert result[user.id].push_enabled is True

    def test_bulk_resolve_multiple_users(
        self, user, other_user, notification_type_social
    ):
        """resolve_bulk resolves for multiple users efficiently."""
        result = PreferenceResolver.resolve_bulk(
            [user.id, other_user.id], notification_type_social
        )

        assert len(result) == 2
        assert user.id in result
        assert other_user.id in result

    def test_bulk_resolve_respects_global_prefs(
        self, user, other_user, notification_type_social
    ):
        """Bulk resolution respects individual global preferences."""
        # Only user has global disabled
        UserGlobalPreference.objects.create(user=user, all_disabled=True)

        result = PreferenceResolver.resolve_bulk(
            [user.id, other_user.id], notification_type_social
        )

        assert result[user.id].blocked is True
        assert result[other_user.id].blocked is False

    def test_bulk_resolve_respects_category_prefs(
        self, user, other_user, notification_type_social
    ):
        """Bulk resolution respects individual category preferences."""
        UserCategoryPreference.objects.create(
            user=user,
            category=NotificationCategory.SOCIAL,
            disabled=True,
        )

        result = PreferenceResolver.resolve_bulk(
            [user.id, other_user.id], notification_type_social
        )

        assert result[user.id].blocked is True
        assert result[other_user.id].blocked is False

    def test_bulk_resolve_respects_type_prefs(
        self, user, other_user, notification_type_social
    ):
        """Bulk resolution respects individual type preferences."""
        UserNotificationPreference.objects.create(
            user=user,
            notification_type=notification_type_social,
            push_enabled=False,
        )

        result = PreferenceResolver.resolve_bulk(
            [user.id, other_user.id], notification_type_social
        )

        assert result[user.id].push_enabled is False
        assert result[other_user.id].push_enabled is True

    def test_bulk_resolve_uses_three_queries(
        self, user, other_user, notification_type_social, django_assert_num_queries
    ):
        """Bulk resolution uses exactly 3 queries regardless of user count."""
        user_ids = [user.id, other_user.id]

        # Should be exactly 3 queries:
        # 1. UserGlobalPreference.objects.filter(user_id__in=...)
        # 2. UserCategoryPreference.objects.filter(user_id__in=..., category=...)
        # 3. UserNotificationPreference.objects.filter(user_id__in=..., notification_type=...)
        with django_assert_num_queries(3):
            PreferenceResolver.resolve_bulk(user_ids, notification_type_social)
