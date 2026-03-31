"""Lightweight circuit breaker for detecting repeated failures.

Tracks consecutive failures per named component (e.g. "market_data",
"strategy").  When a component exceeds the failure threshold it is
considered "tripped" — callers should skip that component until it
recovers.

The breaker auto-recovers after *recovery_seconds* of no new failures.
"""

import logging
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Per-component failure tracking with automatic recovery."""

    def __init__(self, threshold: int = 5, recovery_seconds: float = 60.0) -> None:
        self.threshold = threshold
        self.recovery_seconds = recovery_seconds
        # component -> (consecutive_failures, last_failure_monotonic)
        self._state: Dict[str, Tuple[int, float]] = {}

    def record_failure(self, component: str) -> None:
        """Record a failure for *component*.  Logs a warning when the
        threshold is first breached."""
        failures, _ = self._state.get(component, (0, 0.0))
        failures += 1
        self._state[component] = (failures, time.monotonic())

        if failures == self.threshold:
            logger.warning(
                "Circuit breaker tripped for '%s' after %d consecutive failures "
                "(will auto-recover after %.0fs of no failures)",
                component, failures, self.recovery_seconds,
            )
        elif failures > self.threshold:
            logger.debug(
                "Circuit breaker '%s': %d consecutive failures", component, failures,
            )

    def record_success(self, component: str) -> None:
        """Reset the failure counter for *component* on success."""
        if component in self._state:
            del self._state[component]

    def is_tripped(self, component: str) -> bool:
        """Return True if *component* has exceeded the failure threshold
        and has not yet auto-recovered."""
        if component not in self._state:
            return False
        failures, last_failure = self._state[component]
        if failures < self.threshold:
            return False
        # Auto-recover after recovery_seconds of no new failures
        if time.monotonic() - last_failure >= self.recovery_seconds:
            logger.info("Circuit breaker '%s' auto-recovered", component)
            del self._state[component]
            return False
        return True

    def get_status(self) -> Dict[str, Dict]:
        """Return a snapshot of all tracked components for logging."""
        now = time.monotonic()
        status: Dict[str, Dict] = {}
        for component, (failures, last_failure) in self._state.items():
            status[component] = {
                "consecutive_failures": failures,
                "tripped": failures >= self.threshold,
                "seconds_since_last_failure": round(now - last_failure, 1),
            }
        return status
