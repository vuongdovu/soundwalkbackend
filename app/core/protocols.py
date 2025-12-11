"""
Protocol definitions for generic infrastructure services.

This module defines Protocol classes that specify interfaces
for generic infrastructure concerns like caching.

Protocols define contracts that services must fulfill, enabling:
- Duck typing with static type checking
- Dependency inversion (depend on abstractions, not concretions)
- Easy mocking in tests

Available Protocols:
    CacheBackend: Cache operations interface

Usage:
    from core.protocols import CacheBackend

    def cached_operation(cache: CacheBackend, key: str):
        value = cache.get(key)
        if value is None:
            value = expensive_computation()
            cache.set(key, value, timeout=3600)
        return value

    class RedisCache:
        def get(self, key, default=None): ...
        def set(self, key, value, timeout=None): ...
        def delete(self, key): ...
        def clear(self): ...

    # RedisCache is a valid CacheBackend
    # even without explicit inheritance (duck typing)
    cache: CacheBackend = RedisCache()

Note:
    - Protocols are primarily for type checking
    - @runtime_checkable allows isinstance() checks
    - For domain-specific protocols (email, payments, notifications), see toolkit.protocols
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Any


@runtime_checkable
class CacheBackend(Protocol):
    """
    Protocol for cache backends.

    Defines the interface for cache operations.
    Compatible with Django's cache interface.

    Example:
        class RedisCache:
            def get(self, key, default=None): ...
            def set(self, key, value, timeout=None): ...
            def delete(self, key): ...
            def clear(self): ...

        def cached_operation(cache: CacheBackend, key: str):
            value = cache.get(key)
            if value is None:
                value = expensive_computation()
                cache.set(key, value, timeout=3600)
            return value
    """

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get value from cache.

        Args:
            key: Cache key
            default: Value to return if key not found

        Returns:
            Cached value or default
        """
        ...

    def set(self, key: str, value: Any, timeout: int | None = None) -> None:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            timeout: Expiration time in seconds (None for no expiry)
        """
        ...

    def delete(self, key: str) -> bool:
        """
        Delete value from cache.

        Args:
            key: Cache key to delete

        Returns:
            True if key was deleted, False if it didn't exist
        """
        ...

    def clear(self) -> None:
        """Clear all cached values."""
        ...
