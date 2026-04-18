"""Lightweight TTL cache for reducing redundant API calls within a bot cycle.

Provides two classes:
- TTLCacheEntry: single-value cache with TTL (e.g. meta, user_state)
- TTLCacheMap: keyed cache with TTL (e.g. per-coin market data, per-DEX mids)

Both use time.monotonic() for timing.
"""

import time
from typing import Generic, Hashable, Optional, TypeVar

T = TypeVar('T')
K = TypeVar('K', bound=Hashable)


class TTLCacheEntry(Generic[T]):
    """Single-value cache with TTL."""

    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._value: Optional[T] = None
        self._time: float = 0.0

    def get(self) -> Optional[T]:
        """Return cached value if still fresh, else None."""
        if self._value is not None and (time.monotonic() - self._time) < self.ttl:
            return self._value
        return None

    def set(self, value: T) -> None:
        """Store a value with the current timestamp."""
        self._value = value
        self._time = time.monotonic()

    def invalidate(self) -> None:
        """Clear the cached value."""
        self._value = None


class TTLCacheMap(Generic[K, T]):
    """Keyed cache where each entry has a shared TTL."""

    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._data: dict[K, tuple[float, T]] = {}

    def get(self, key: K) -> Optional[T]:
        """Return cached value for *key* if still fresh, else None."""
        entry = self._data.get(key)
        if entry is not None and (time.monotonic() - entry[0]) < self.ttl:
            return entry[1]
        return None

    def set(self, key: K, value: T) -> None:
        """Store a value for *key* with the current timestamp."""
        self._data[key] = (time.monotonic(), value)

    def invalidate(self, key: K) -> None:
        """Remove a single key from the cache."""
        self._data.pop(key, None)

    def invalidate_all(self) -> None:
        """Clear all cached entries."""
        self._data.clear()
