"""
Tests for the CircuitBreaker class.

These tests verify the distributed circuit breaker behavior including:
- State transitions (closed -> open -> half-open -> closed)
- Failure counting and threshold detection
- Recovery timeout handling
- Context manager usage
- Multi-worker behavior via cache backend
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache

from core.circuit_breaker import CircuitBreaker, CircuitOpenError


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test to ensure isolation."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def circuit():
    """Create a circuit breaker with test-friendly settings."""
    return CircuitBreaker(
        name="test-service",
        failure_threshold=3,
        recovery_timeout=5,
        half_open_max_calls=1,
    )


class TestCircuitBreakerInitialState:
    """Test circuit breaker initial state."""

    def test_starts_in_closed_state(self, circuit: CircuitBreaker):
        """Circuit should start in closed state."""
        status = circuit.get_status()
        assert status["state"] == "closed"
        assert status["is_available"] is True

    def test_is_available_returns_true_initially(self, circuit: CircuitBreaker):
        """New circuit breaker should allow calls."""
        assert circuit.is_available() is True

    def test_initial_failure_count_is_zero(self, circuit: CircuitBreaker):
        """Failure count should be zero initially."""
        status = circuit.get_status()
        assert status["failure_count"] == 0


class TestCircuitBreakerFailureTracking:
    """Test failure counting and threshold behavior."""

    def test_single_failure_does_not_open_circuit(self, circuit: CircuitBreaker):
        """Single failure should not open the circuit."""
        circuit.record_failure()

        assert circuit.is_available() is True
        status = circuit.get_status()
        assert status["failure_count"] == 1
        assert status["state"] == "closed"

    def test_failures_below_threshold_keep_circuit_closed(
        self, circuit: CircuitBreaker
    ):
        """Failures below threshold should keep circuit closed."""
        # Record failures just below threshold (threshold is 3)
        circuit.record_failure()
        circuit.record_failure()

        assert circuit.is_available() is True
        status = circuit.get_status()
        assert status["failure_count"] == 2
        assert status["state"] == "closed"

    def test_reaching_threshold_opens_circuit(self, circuit: CircuitBreaker):
        """Circuit should open when failure threshold is reached."""
        # Record failures to reach threshold
        for _ in range(3):
            circuit.record_failure()

        assert circuit.is_available() is False
        status = circuit.get_status()
        assert status["state"] == "open"

    def test_success_resets_failure_count(self, circuit: CircuitBreaker):
        """Successful call should reset failure count."""
        circuit.record_failure()
        circuit.record_failure()
        assert circuit.get_status()["failure_count"] == 2

        circuit.record_success()

        assert circuit.get_status()["failure_count"] == 0
        assert circuit.is_available() is True


class TestCircuitBreakerRecovery:
    """Test circuit breaker recovery behavior."""

    def test_open_circuit_rejects_calls(self, circuit: CircuitBreaker):
        """Open circuit should reject calls."""
        # Open the circuit
        for _ in range(3):
            circuit.record_failure()

        assert circuit.is_available() is False

    def test_circuit_transitions_to_half_open_after_timeout(
        self, circuit: CircuitBreaker
    ):
        """Circuit should transition to half-open after recovery timeout."""
        # Open the circuit
        for _ in range(3):
            circuit.record_failure()
        assert circuit.get_status()["state"] == "open"

        # Get the actual opened_at time from cache
        opened_at = circuit._get_opened_at()
        assert opened_at is not None

        # Mock time.time() to return a time after recovery timeout
        with patch("core.circuit_breaker.time.time") as mock_time:
            mock_time.return_value = (
                opened_at + 6
            )  # 6 seconds after opening (timeout is 5)

            assert circuit.is_available() is True
            assert circuit.get_status()["state"] == "half_open"

    def test_successful_half_open_call_closes_circuit(self, circuit: CircuitBreaker):
        """Successful call in half-open state should close the circuit."""
        # Open the circuit
        for _ in range(3):
            circuit.record_failure()

        # Get the actual opened_at time from cache
        opened_at = circuit._get_opened_at()

        # Transition to half-open by waiting
        with patch("core.circuit_breaker.time.time") as mock_time:
            mock_time.return_value = opened_at + 6
            circuit.is_available()  # Triggers transition

            # Record success in half-open state (still within mock context)
            circuit.record_success()

            assert circuit.get_status()["state"] == "closed"
            assert circuit.is_available() is True

    def test_failed_half_open_call_reopens_circuit(self, circuit: CircuitBreaker):
        """Failed call in half-open state should reopen the circuit."""
        # Open the circuit
        for _ in range(3):
            circuit.record_failure()

        # Get the actual opened_at time from cache
        opened_at = circuit._get_opened_at()

        # Transition to half-open and test failure
        with patch("core.circuit_breaker.time.time") as mock_time:
            mock_time.return_value = opened_at + 6
            circuit.is_available()  # Triggers transition to half-open

            # Record failure in half-open state
            circuit.record_failure()

            assert circuit.get_status()["state"] == "open"
            assert circuit.is_available() is False


class TestCircuitBreakerContextManager:
    """Test context manager usage."""

    def test_context_manager_records_success(self, circuit: CircuitBreaker):
        """Context manager should record success on normal completion."""
        with circuit.call():
            pass  # Simulated successful call

        assert circuit.get_status()["failure_count"] == 0

    def test_context_manager_records_failure_on_exception(
        self, circuit: CircuitBreaker
    ):
        """Context manager should record failure on exception."""
        with pytest.raises(ValueError):
            with circuit.call():
                raise ValueError("Simulated error")

        assert circuit.get_status()["failure_count"] == 1

    def test_context_manager_raises_circuit_open_error_when_open(
        self, circuit: CircuitBreaker
    ):
        """Context manager should raise CircuitOpenError when circuit is open."""
        # Open the circuit
        for _ in range(3):
            circuit.record_failure()

        with pytest.raises(CircuitOpenError) as exc_info:
            with circuit.call():
                pass

        assert "test-service" in str(exc_info.value)

    def test_circuit_open_error_does_not_count_as_failure(
        self, circuit: CircuitBreaker
    ):
        """CircuitOpenError should not be counted as a failure."""
        # Open the circuit
        for _ in range(3):
            circuit.record_failure()

        failure_count_before = circuit.get_status()["failure_count"]

        with pytest.raises(CircuitOpenError):
            with circuit.call():
                pass

        # Failure count should not increase
        assert circuit.get_status()["failure_count"] == failure_count_before


class TestCircuitBreakerReset:
    """Test manual reset functionality."""

    def test_reset_closes_open_circuit(self, circuit: CircuitBreaker):
        """Reset should close an open circuit."""
        # Open the circuit
        for _ in range(3):
            circuit.record_failure()
        assert circuit.get_status()["state"] == "open"

        circuit.reset()

        assert circuit.get_status()["state"] == "closed"
        assert circuit.is_available() is True

    def test_reset_clears_failure_count(self, circuit: CircuitBreaker):
        """Reset should clear the failure count."""
        circuit.record_failure()
        circuit.record_failure()
        assert circuit.get_status()["failure_count"] == 2

        circuit.reset()

        assert circuit.get_status()["failure_count"] == 0


class TestCircuitBreakerDistributedState:
    """Test distributed behavior via cache backend."""

    def test_state_shared_across_instances(self):
        """Multiple circuit breaker instances should share state."""
        circuit1 = CircuitBreaker(
            name="shared-service",
            failure_threshold=3,
            recovery_timeout=5,
        )
        circuit2 = CircuitBreaker(
            name="shared-service",
            failure_threshold=3,
            recovery_timeout=5,
        )

        # Record failures on instance 1
        for _ in range(3):
            circuit1.record_failure()

        # Instance 2 should see the open circuit
        assert circuit2.is_available() is False
        assert circuit2.get_status()["state"] == "open"

    def test_different_circuits_are_independent(self):
        """Circuits with different names should be independent."""
        circuit_a = CircuitBreaker(name="service-a", failure_threshold=3)
        circuit_b = CircuitBreaker(name="service-b", failure_threshold=3)

        # Open circuit A
        for _ in range(3):
            circuit_a.record_failure()

        # Circuit B should still be available
        assert circuit_a.is_available() is False
        assert circuit_b.is_available() is True


class TestCircuitBreakerCacheFailure:
    """Test behavior when cache is unavailable."""

    def test_is_available_returns_true_on_cache_error(self, circuit: CircuitBreaker):
        """Should fail open if cache is unavailable."""
        with patch.object(cache, "get", side_effect=Exception("Cache error")):
            # Should not raise, and should return True (fail open)
            assert circuit.is_available() is True

    def test_record_success_handles_cache_error(self, circuit: CircuitBreaker):
        """record_success should not raise on cache error."""
        with patch.object(cache, "get", side_effect=Exception("Cache error")):
            # Should not raise
            circuit.record_success()

    def test_record_failure_handles_cache_error(self, circuit: CircuitBreaker):
        """record_failure should not raise on cache error."""
        with patch.object(cache, "get", side_effect=Exception("Cache error")):
            # Should not raise
            circuit.record_failure()


class TestCircuitBreakerStatus:
    """Test status reporting."""

    def test_status_includes_all_fields(self, circuit: CircuitBreaker):
        """Status should include all relevant fields."""
        status = circuit.get_status()

        assert "name" in status
        assert "state" in status
        assert "failure_count" in status
        assert "failure_threshold" in status
        assert "is_available" in status

    def test_status_includes_timing_when_open(self, circuit: CircuitBreaker):
        """Status should include timing info when circuit is open."""
        # Open the circuit
        for _ in range(3):
            circuit.record_failure()

        status = circuit.get_status()

        assert "opened_seconds_ago" in status
        assert "recovery_in_seconds" in status

    def test_repr_includes_name_and_state(self, circuit: CircuitBreaker):
        """String representation should include name and state."""
        repr_str = repr(circuit)

        assert "test-service" in repr_str
        assert "closed" in repr_str


class TestCircuitBreakerHalfOpenCalls:
    """Test half-open state call limiting."""

    def test_limits_calls_in_half_open_state(self):
        """Should limit number of calls in half-open state."""
        circuit = CircuitBreaker(
            name="limited-service",
            failure_threshold=3,
            recovery_timeout=1,
            half_open_max_calls=2,
        )

        # Open the circuit
        for _ in range(3):
            circuit.record_failure()

        # Get the actual opened_at time from cache
        opened_at = circuit._get_opened_at()

        # Wait for recovery timeout
        with patch("core.circuit_breaker.time.time") as mock_time:
            mock_time.return_value = opened_at + 2

            # First two calls should be allowed (half_open_max_calls=2)
            assert circuit.is_available() is True  # Call 1
            assert circuit.is_available() is True  # Call 2

            # Third call should be rejected
            assert circuit.is_available() is False
