"""Unit tests for CircuitBreaker."""

import time
from circuit_breaker import CircuitBreaker


class TestCircuitBreaker:

    def test_not_tripped_initially(self):
        cb = CircuitBreaker(threshold=3)
        assert cb.is_tripped("x") is False

    def test_not_tripped_below_threshold(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure("x")
        cb.record_failure("x")
        assert cb.is_tripped("x") is False

    def test_tripped_at_threshold(self):
        cb = CircuitBreaker(threshold=3)
        for _ in range(3):
            cb.record_failure("x")
        assert cb.is_tripped("x") is True

    def test_success_resets(self):
        cb = CircuitBreaker(threshold=2)
        cb.record_failure("x")
        cb.record_failure("x")
        assert cb.is_tripped("x") is True
        cb.record_success("x")
        assert cb.is_tripped("x") is False

    def test_auto_recovery(self):
        cb = CircuitBreaker(threshold=2, recovery_seconds=0.1)
        cb.record_failure("x")
        cb.record_failure("x")
        assert cb.is_tripped("x") is True
        time.sleep(0.15)
        assert cb.is_tripped("x") is False

    def test_independent_components(self):
        cb = CircuitBreaker(threshold=2)
        cb.record_failure("a")
        cb.record_failure("a")
        cb.record_failure("b")
        assert cb.is_tripped("a") is True
        assert cb.is_tripped("b") is False

    def test_get_status_empty(self):
        cb = CircuitBreaker()
        assert cb.get_status() == {}

    def test_get_status_with_failures(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure("x")
        status = cb.get_status()
        assert "x" in status
        assert status["x"]["consecutive_failures"] == 1
        assert status["x"]["tripped"] is False

    def test_get_status_tripped(self):
        cb = CircuitBreaker(threshold=2)
        cb.record_failure("x")
        cb.record_failure("x")
        status = cb.get_status()
        assert status["x"]["tripped"] is True
        assert status["x"]["consecutive_failures"] == 2
