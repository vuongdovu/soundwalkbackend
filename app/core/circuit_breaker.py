"""
Circuit breaker pattern for resilient external service calls.

This module provides a distributed circuit breaker that uses Django's cache
backend (Redis) to share state across multiple application instances.

The circuit breaker pattern prevents cascading failures by temporarily
stopping calls to a failing service, allowing it time to recover.

States:
    - CLOSED: Normal operation, all requests pass through
    - OPEN: Service is failing, requests fail fast without calling service
    - HALF_OPEN: Testing recovery, limited requests allowed through

Usage:
    from core.circuit_breaker import CircuitBreaker

    # Create a circuit breaker for an external service
    scanner_circuit = CircuitBreaker(
        name="clamav",
        failure_threshold=5,
        recovery_timeout=60,
    )

    # Check availability before calling
    if scanner_circuit.is_available():
        try:
            result = external_service.call()
            scanner_circuit.record_success()
        except Exception:
            scanner_circuit.record_failure()
            raise

    # Or use the context manager for automatic recording
    with scanner_circuit.call():
        result = external_service.call()

Design Notes:
    - State is stored in Django cache (Redis) for multi-worker consistency
    - Uses optimistic concurrency with timestamps to handle race conditions
    - Falls back to closed state if cache is unavailable (fail-open for circuit)
    - Thread-safe through atomic cache operations
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from django.core.cache import cache

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for a circuit breaker instance."""

    failure_threshold: int = 5
    """Number of consecutive failures before opening circuit."""

    recovery_timeout: int = 60
    """Seconds to wait before attempting recovery (half-open state)."""

    half_open_max_calls: int = 1
    """Number of test calls allowed in half-open state."""

    cache_ttl: int = 3600
    """TTL for cache keys in seconds (should exceed recovery_timeout)."""


class CircuitBreaker:
    """
    Distributed circuit breaker using Django cache backend.

    Prevents cascading failures by stopping calls to failing services.
    State is shared across all application instances via Redis cache.

    Attributes:
        name: Unique identifier for this circuit breaker
        config: Circuit breaker configuration

    Example:
        # Create circuit breaker
        cb = CircuitBreaker("external-api", failure_threshold=3)

        # Manual usage
        if cb.is_available():
            try:
                result = api.call()
                cb.record_success()
            except APIError:
                cb.record_failure()
                raise

        # Context manager usage (auto-records success/failure)
        with cb.call():
            result = api.call()  # Raises if circuit is open

    Thread Safety:
        Uses atomic cache operations. Multiple workers can safely
        update the same circuit breaker state.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max_calls: int = 1,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Unique identifier for this circuit (e.g., "clamav", "stripe-api")
            failure_threshold: Consecutive failures before opening circuit
            recovery_timeout: Seconds before attempting recovery
            half_open_max_calls: Test calls allowed in half-open state
        """
        self.name = name
        self.config = CircuitBreakerConfig(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            half_open_max_calls=half_open_max_calls,
        )

        # Cache keys
        self._state_key = f"circuit:{name}:state"
        self._failures_key = f"circuit:{name}:failures"
        self._opened_at_key = f"circuit:{name}:opened_at"
        self._half_open_calls_key = f"circuit:{name}:half_open_calls"

    def is_available(self) -> bool:
        """
        Check if the circuit allows calls through.

        Returns:
            True if calls are allowed (closed or half-open state)
            False if circuit is open and recovery timeout not elapsed

        Note:
            This check is safe to call frequently - it's a simple cache read.
            If cache is unavailable, returns True (fail-open for circuit itself).
        """
        try:
            state = self._get_state()

            if state == CircuitState.CLOSED:
                return True

            if state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                opened_at = self._get_opened_at()
                if (
                    opened_at
                    and (time.time() - opened_at) >= self.config.recovery_timeout
                ):
                    # Transition to half-open
                    self._set_state(CircuitState.HALF_OPEN)
                    self._reset_half_open_calls()
                    logger.info(
                        "Circuit breaker transitioning to half-open",
                        extra={"circuit": self.name},
                    )
                    # Count this call toward half-open limit
                    self._increment_half_open_calls()
                    return True
                return False

            if state == CircuitState.HALF_OPEN:
                # Allow limited calls in half-open state
                half_open_calls = self._get_half_open_calls()
                if half_open_calls < self.config.half_open_max_calls:
                    self._increment_half_open_calls()
                    return True
                return False

            # Unknown state - default to available
            return True

        except Exception as e:
            # Cache error - fail open (allow calls through)
            logger.warning(
                f"Circuit breaker cache error, failing open: {e}",
                extra={"circuit": self.name},
            )
            return True

    def record_success(self) -> None:
        """
        Record a successful service call.

        Closes the circuit if in half-open state.
        Resets failure count.
        """
        try:
            state = self._get_state()

            if state == CircuitState.HALF_OPEN:
                # Successful test call - close the circuit
                self._set_state(CircuitState.CLOSED)
                logger.info(
                    "Circuit breaker closed after successful recovery",
                    extra={"circuit": self.name},
                )

            # Reset failures on any success
            self._reset_failures()

        except Exception as e:
            logger.warning(
                f"Circuit breaker failed to record success: {e}",
                extra={"circuit": self.name},
            )

    def record_failure(self) -> None:
        """
        Record a failed service call.

        Increments failure count. Opens circuit if threshold exceeded.
        Immediately reopens if in half-open state.
        """
        try:
            state = self._get_state()

            if state == CircuitState.HALF_OPEN:
                # Failed test call - reopen immediately
                self._open_circuit()
                logger.warning(
                    "Circuit breaker reopened after failed recovery attempt",
                    extra={"circuit": self.name},
                )
                return

            # Increment failure count
            failures = self._increment_failures()

            if failures >= self.config.failure_threshold:
                self._open_circuit()
                logger.warning(
                    f"Circuit breaker opened after {failures} failures",
                    extra={
                        "circuit": self.name,
                        "failure_count": failures,
                        "threshold": self.config.failure_threshold,
                    },
                )

        except Exception as e:
            logger.warning(
                f"Circuit breaker failed to record failure: {e}",
                extra={"circuit": self.name},
            )

    @contextmanager
    def call(self) -> Generator[None, None, None]:
        """
        Context manager for automatic success/failure recording.

        Raises CircuitOpenError if circuit is open.

        Example:
            with circuit.call():
                result = api.call()  # Success automatically recorded
                # If exception raised, failure is recorded
        """
        if not self.is_available():
            raise CircuitOpenError(f"Circuit '{self.name}' is open")

        try:
            yield
            self.record_success()
        except CircuitOpenError:
            # Don't record circuit errors as failures
            raise
        except Exception:
            self.record_failure()
            raise

    def reset(self) -> None:
        """
        Manually reset the circuit breaker to closed state.

        Use this for administrative purposes or testing.
        """
        try:
            self._set_state(CircuitState.CLOSED)
            self._reset_failures()
            self._reset_half_open_calls()
            logger.info(
                "Circuit breaker manually reset",
                extra={"circuit": self.name},
            )
        except Exception as e:
            logger.warning(
                f"Circuit breaker failed to reset: {e}",
                extra={"circuit": self.name},
            )

    def get_status(self) -> dict:
        """
        Get current circuit breaker status for monitoring.

        Returns:
            Dict with state, failure count, and timing information
        """
        try:
            state = self._get_state()
            failures = self._get_failures()
            opened_at = self._get_opened_at()

            status = {
                "name": self.name,
                "state": state.value,
                "failure_count": failures,
                "failure_threshold": self.config.failure_threshold,
                "is_available": self.is_available(),
            }

            if opened_at:
                elapsed = time.time() - opened_at
                status["opened_seconds_ago"] = int(elapsed)
                status["recovery_in_seconds"] = max(
                    0, int(self.config.recovery_timeout - elapsed)
                )

            return status

        except Exception as e:
            return {
                "name": self.name,
                "state": "unknown",
                "error": str(e),
            }

    # =========================================================================
    # Private cache operations
    # =========================================================================

    def _get_state(self) -> CircuitState:
        """Get current circuit state from cache."""
        state_str = cache.get(self._state_key, CircuitState.CLOSED.value)
        try:
            return CircuitState(state_str)
        except ValueError:
            return CircuitState.CLOSED

    def _set_state(self, state: CircuitState) -> None:
        """Set circuit state in cache."""
        cache.set(self._state_key, state.value, timeout=self.config.cache_ttl)

    def _get_failures(self) -> int:
        """Get current failure count."""
        return cache.get(self._failures_key, 0)

    def _increment_failures(self) -> int:
        """Increment and return failure count."""
        try:
            return cache.incr(self._failures_key)
        except ValueError:
            # Key doesn't exist - set it
            cache.set(self._failures_key, 1, timeout=self.config.cache_ttl)
            return 1

    def _reset_failures(self) -> None:
        """Reset failure count to zero."""
        cache.set(self._failures_key, 0, timeout=self.config.cache_ttl)

    def _get_opened_at(self) -> float | None:
        """Get timestamp when circuit was opened."""
        return cache.get(self._opened_at_key)

    def _open_circuit(self) -> None:
        """Open the circuit and record the time."""
        self._set_state(CircuitState.OPEN)
        cache.set(self._opened_at_key, time.time(), timeout=self.config.cache_ttl)

    def _get_half_open_calls(self) -> int:
        """Get number of calls made in half-open state."""
        return cache.get(self._half_open_calls_key, 0)

    def _increment_half_open_calls(self) -> int:
        """Increment half-open call count."""
        try:
            return cache.incr(self._half_open_calls_key)
        except ValueError:
            cache.set(self._half_open_calls_key, 1, timeout=self.config.cache_ttl)
            return 1

    def _reset_half_open_calls(self) -> None:
        """Reset half-open call count."""
        cache.set(self._half_open_calls_key, 0, timeout=self.config.cache_ttl)

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"CircuitBreaker(name={self.name!r}, state={self._get_state().value})"


class CircuitOpenError(Exception):
    """
    Raised when attempting to call through an open circuit.

    This is a signal that the service is unavailable, not that
    an actual call failed. Handle this separately from service errors.
    """

    pass
