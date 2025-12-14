"""
Concurrency control utilities for payment operations.

This module provides two complementary concurrency mechanisms:

1. **Distributed Locks** (DistributedLock)
   - Redis-based mutual exclusion across processes/servers
   - TTL prevents deadlocks from crashed processes
   - Context manager support for clean usage
   - Use for: operations spanning multiple records, external API calls

2. **Optimistic Locking** (check_version)
   - Version-based conflict detection
   - No blocking - detect conflicts at write time
   - Use for: single-record updates, webhook handlers

Usage:

    # Distributed lock (for multi-step operations)
    from payments.locks import DistributedLock

    with DistributedLock(f"payment:{order_id}", ttl=30):
        # Only one process can execute this at a time
        process_payment(order_id)

    # Optimistic locking (for single-record updates)
    from payments.locks import check_version

    with transaction.atomic():
        order = check_version(PaymentOrder, order_id, expected_version=3)
        order.state = "captured"
        order.save()  # Version auto-increments

Note:
    These utilities are designed to work together. Use distributed locks
    for coarse-grained synchronization and optimistic locking for
    fine-grained conflict detection.
"""

from __future__ import annotations

import time
import uuid as uuid_module
from typing import TYPE_CHECKING, TypeVar

from django.db import models, transaction

from django_redis import get_redis_connection

from core.exceptions import NotFoundError
from payments.exceptions import LockAcquisitionError, StaleRecordError

if TYPE_CHECKING:
    from typing import Any

    from redis import Redis

# Type variable for model classes
T = TypeVar("T", bound=models.Model)


# =============================================================================
# Distributed Locks
# =============================================================================


class DistributedLock:
    """
    Redis-based distributed lock with TTL.

    Provides mutual exclusion across multiple processes/servers
    for critical payment operations.

    Features:
        - Automatic TTL prevents deadlocks from crashed processes
        - Token-based ownership prevents accidental release by other processes
        - Blocking and non-blocking acquisition modes
        - Context manager support for clean usage
        - Lock extension for long-running operations

    Example:
        # Context manager (recommended)
        with DistributedLock("payment:order:123", ttl=30):
            process_payment()

        # Manual acquire/release
        lock = DistributedLock("payment:order:123", ttl=30, blocking=False)
        if lock.acquire():
            try:
                process_payment()
            finally:
                lock.release()

        # With timeout
        lock = DistributedLock("payout:456", ttl=60, blocking=True, timeout=5.0)
        try:
            with lock:
                execute_payout()
        except LockAcquisitionError:
            # Another process holds the lock
            handle_contention()

    Args:
        key: Lock identifier (will be prefixed with "lock:")
        ttl: Lock TTL in seconds (auto-releases after this time)
        blocking: If True, acquire() waits until lock is available
        timeout: Maximum wait time in seconds (only if blocking=True)

    Note:
        The TTL should be longer than the expected operation duration.
        Use extend() for operations that might take longer than expected.
    """

    # Lua script for atomic check-and-delete (release)
    RELEASE_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    # Lua script for atomic check-and-extend
    EXTEND_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("expire", KEYS[1], ARGV[2])
    else
        return 0
    end
    """

    def __init__(
        self,
        key: str,
        ttl: int = 30,
        blocking: bool = True,
        timeout: float = 10.0,
    ) -> None:
        """
        Initialize the distributed lock.

        Args:
            key: Lock identifier (will be prefixed with "lock:")
            ttl: Lock TTL in seconds (default: 30)
            blocking: If True, acquire() waits until available (default: True)
            timeout: Max wait time in seconds for blocking mode (default: 10.0)
        """
        self.key = f"lock:{key}"
        self.ttl = ttl
        self.blocking = blocking
        self.timeout = timeout
        self._token: str | None = None
        self._redis: Redis | None = None

    def _get_redis(self) -> Redis:
        """Get Redis connection (lazy initialization)."""
        if self._redis is None:
            self._redis = get_redis_connection("default")
        return self._redis

    def acquire(self) -> bool:
        """
        Attempt to acquire the lock.

        Returns:
            True if lock was acquired

        Raises:
            LockAcquisitionError: If lock couldn't be acquired
        """
        self._token = str(uuid_module.uuid4())
        redis = self._get_redis()

        if self.blocking:
            end_time = time.time() + self.timeout
            while time.time() < end_time:
                if self._try_acquire(redis):
                    return True
                time.sleep(0.05)  # 50ms between retries

            raise LockAcquisitionError(
                f"Failed to acquire lock '{self.key}' within {self.timeout}s",
                details={"key": self.key, "timeout": self.timeout},
            )

        if not self._try_acquire(redis):
            raise LockAcquisitionError(
                f"Lock '{self.key}' is already held",
                details={"key": self.key},
            )
        return True

    def _try_acquire(self, redis: Redis) -> bool:
        """Try once to acquire the lock."""
        return bool(redis.set(self.key, self._token, nx=True, ex=self.ttl))

    def release(self) -> bool:
        """
        Release the lock if we hold it.

        Returns:
            True if lock was released, False if we didn't hold it

        Note:
            Safe to call multiple times. Uses atomic Lua script to
            ensure we only release if we own the lock.
        """
        if self._token is None:
            return False

        redis = self._get_redis()
        result = redis.eval(self.RELEASE_SCRIPT, 1, self.key, self._token)
        self._token = None
        return bool(result)

    def extend(self, additional_ttl: int | None = None) -> bool:
        """
        Extend the lock TTL if we hold it.

        Use this for operations that may take longer than the initial TTL.
        The new TTL replaces the remaining time (not added to it).

        Args:
            additional_ttl: New TTL in seconds (defaults to original TTL)

        Returns:
            True if lock was extended, False if we don't hold it

        Example:
            with DistributedLock("long:operation", ttl=30) as lock:
                for chunk in large_dataset:
                    process(chunk)
                    lock.extend()  # Reset TTL for each chunk
        """
        if self._token is None:
            return False

        ttl = additional_ttl or self.ttl
        redis = self._get_redis()
        result = redis.eval(self.EXTEND_SCRIPT, 1, self.key, self._token, ttl)
        return bool(result)

    @property
    def is_held(self) -> bool:
        """Check if we currently hold the lock."""
        return self._token is not None

    def __enter__(self) -> DistributedLock:
        """Context manager entry - acquire the lock."""
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        """Context manager exit - always release the lock."""
        self.release()
        return False  # Don't suppress exceptions


# =============================================================================
# Optimistic Locking
# =============================================================================


def check_version(
    model_class: type[T],
    pk: Any,
    expected_version: int,
) -> T:
    """
    Atomically check version and lock a record for update.

    Combines optimistic locking (version check) with pessimistic locking
    (select_for_update) for the actual update operation. This prevents
    race conditions when multiple processes try to modify the same record.

    Args:
        model_class: Django model class (must have 'version' field)
        pk: Primary key of the record
        expected_version: Version the caller expects

    Returns:
        The locked model instance (within a transaction)

    Raises:
        StaleRecordError: If version doesn't match (concurrent modification)
        NotFoundError: If record doesn't exist

    Example:
        with transaction.atomic():
            # Lock the record and verify version
            order = check_version(PaymentOrder, order_id, version=3)

            # Safe to modify - we have exclusive access
            order.state = "processing"
            order.save()  # Version auto-increments to 4

    Note:
        Must be called within a transaction context. The lock is held
        until the transaction commits or rolls back.

    Warning:
        The version field on the model must be incremented on every save.
        This is typically done in the model's save() method using F():

            def save(self, *args, **kwargs):
                if self.pk:
                    self.version = F('version') + 1
                super().save(*args, **kwargs)
    """
    with transaction.atomic():
        # Try to get the record with expected version and lock it
        instance = (
            model_class.objects.select_for_update()
            .filter(pk=pk, version=expected_version)
            .first()
        )

        if instance is None:
            # Check if record exists at all
            exists = model_class.objects.filter(pk=pk).exists()
            if not exists:
                model_name = model_class.__name__
                raise NotFoundError(
                    f"{model_name} {pk} not found",
                    error_code=f"{model_name.upper()}_NOT_FOUND",
                    details={"pk": str(pk)},
                )

            # Record exists but version doesn't match - concurrent modification
            current = model_class.objects.get(pk=pk)
            model_name = model_class.__name__
            raise StaleRecordError(
                f"{model_name} {pk} has been modified "
                f"(expected version {expected_version}, current {current.version})",
                details={
                    "pk": str(pk),
                    "expected_version": expected_version,
                    "current_version": current.version,
                },
            )

        return instance


__all__ = [
    "DistributedLock",
    "check_version",
]
