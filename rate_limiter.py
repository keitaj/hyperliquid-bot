import os
import time
import logging
from typing import Any, Callable, Optional, Tuple, Type
from threading import Lock
from collections import deque

from hyperliquid.utils.error import Error as HyperliquidAPIError

from exceptions import (
    HyperliquidBotError,
    TransientError,
    RateLimitError,
    NetworkError,
    DataError,
    ConfigurationError,
)

logger = logging.getLogger(__name__)

# Expected exceptions from API calls.  Catch these for graceful degradation;
# programming errors (AttributeError, NameError, etc.) will propagate.
#
# Includes both raw SDK/stdlib exceptions (for code that bypasses
# APICallWrapper) and the custom hierarchy (raised by APICallWrapper.call).
API_ERRORS: Tuple[Type[BaseException], ...] = (
    HyperliquidBotError,  # Custom hierarchy (TransientError, DataError, …)
    HyperliquidAPIError,  # SDK: ClientError (4xx), ServerError (5xx)
    ValueError,           # SDK signing / parameter validation; data parsing
    KeyError,             # Unexpected API response structure
    TypeError,            # Unexpected data types from API
    ConnectionError,      # Network connectivity
    TimeoutError,         # Network timeout
    OSError,              # Low-level I/O (includes requests.RequestException)
)


class RateLimiter:
    """Rate limiter to prevent API rate limit errors"""

    def __init__(self,
                 requests_per_second: float = 2.0,
                 burst_limit: int = 5,
                 backoff_factor: float = 2.0,
                 max_backoff: float = 60.0) -> None:
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second
        self.burst_limit = burst_limit
        self.backoff_factor = backoff_factor
        self.max_backoff = max_backoff

        self._lock = Lock()
        self._last_request_time = 0.0
        self._request_times = deque(maxlen=burst_limit)
        self._consecutive_429s = 0
        self._current_backoff = 0.0

    def wait_if_needed(self) -> None:
        """Wait if necessary to respect rate limits"""
        with self._lock:
            current_time = time.time()

            # Calculate wait time based on minimum interval
            time_since_last = current_time - self._last_request_time
            min_wait = self.min_interval + self._current_backoff

            if time_since_last < min_wait:
                wait_time = min_wait - time_since_last
                logger.debug(f"Rate limiting: waiting {wait_time:.2f}s")
                time.sleep(wait_time)
                current_time = time.time()

            # Check burst limit
            self._request_times.append(current_time)
            if len(self._request_times) >= self.burst_limit:
                oldest_request = self._request_times[0]
                if current_time - oldest_request < 1.0:
                    # Too many requests in the last second
                    burst_wait = 1.0 - (current_time - oldest_request) + 0.1
                    logger.debug(f"Burst limit: waiting {burst_wait:.2f}s")
                    time.sleep(burst_wait)

            self._last_request_time = time.time()

    def on_429_error(self) -> None:
        """Called when a 429 error is received to increase backoff"""
        with self._lock:
            self._consecutive_429s += 1
            self._current_backoff = min(
                self.backoff_factor ** self._consecutive_429s,
                self.max_backoff
            )
            logger.warning(f"429 error #{self._consecutive_429s}, backoff: {self._current_backoff:.2f}s")

    def on_success(self) -> None:
        """Called on successful request to reset backoff"""
        with self._lock:
            if self._consecutive_429s > 0:
                logger.info("Request successful, resetting backoff")
                self._consecutive_429s = 0
                self._current_backoff = 0.0


class APICallWrapper:
    """Wrapper to add rate limiting to API calls with retry on 429 and timeout."""

    MAX_RETRIES = 3

    def __init__(self, rate_limiter: RateLimiter) -> None:
        self.rate_limiter = rate_limiter

    @staticmethod
    def _classify(e: Exception) -> HyperliquidBotError:
        """Wrap a raw exception into the custom hierarchy.

        The original exception is stored as ``__cause__`` so the full
        traceback is preserved.
        """
        error_str = str(e)
        error_lower = error_str.lower()

        def _chain(cls: type) -> HyperliquidBotError:
            wrapped = cls(error_str)
            wrapped.__cause__ = e
            return wrapped

        # Rate-limit detection
        if "429" in error_str or "rate limit" in error_lower:
            return _chain(RateLimitError)

        # Network / timeout detection
        if isinstance(e, (ConnectionError, TimeoutError)):
            return _chain(NetworkError)
        if isinstance(e, OSError):
            return _chain(NetworkError)

        # Data errors — unexpected API response structure
        if isinstance(e, (KeyError, TypeError)):
            return _chain(DataError)

        # SDK / signing errors
        if isinstance(e, ValueError):
            return _chain(ConfigurationError)

        # HyperliquidAPIError — classify by message
        if isinstance(e, HyperliquidAPIError):
            if "429" in error_str or "rate limit" in error_lower:
                return _chain(RateLimitError)
            if any(kw in error_lower for kw in ("timeout", "timed out")):
                return _chain(NetworkError)
            return _chain(DataError)

        # Fallback: treat unknown as transient to be safe
        return _chain(TransientError)

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute API call with rate limiting and automatic retry on 429/timeout."""
        last_exception: Optional[Exception] = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            self.rate_limiter.wait_if_needed()
            try:
                result = func(*args, **kwargs)
                self.rate_limiter.on_success()
                return result
            except Exception as e:
                classified = self._classify(e)
                last_exception = classified

                if isinstance(classified, RateLimitError):
                    self.rate_limiter.on_429_error()
                    if attempt < self.MAX_RETRIES:
                        logger.warning(
                            "Rate limited (attempt %d/%d), retrying after %.1fs",
                            attempt, self.MAX_RETRIES, self.rate_limiter._current_backoff,
                        )
                        time.sleep(self.rate_limiter._current_backoff)
                        continue
                    logger.error("Rate limited after %d attempts, giving up", self.MAX_RETRIES)
                    raise classified

                if isinstance(classified, NetworkError):
                    if attempt < self.MAX_RETRIES:
                        wait = min(2.0 * attempt, 5.0)
                        logger.warning(
                            "Network error (attempt %d/%d), retrying after %.1fs",
                            attempt, self.MAX_RETRIES, wait,
                        )
                        time.sleep(wait)
                        continue
                    logger.error("Network error after %d attempts, giving up", self.MAX_RETRIES)
                    raise classified

                # Non-transient errors (DataError, ConfigurationError): fail immediately
                raise classified

        raise last_exception  # pragma: no cover


# Global rate limiter instance
# Configurable via environment variables. Hyperliquid allows 1,200 weight/minute
# (~20 req/sec for weight-1 requests).
_rate_limit_rps = float(os.getenv("RATE_LIMIT_RPS", "5.0"))
_rate_limit_burst = int(os.getenv("RATE_LIMIT_BURST", "8"))

# Hyperliquid allows 1,200 weight/minute = 20 req/sec for weight-1 requests.
if _rate_limit_rps > 20.0:
    raise ValueError(
        f"RATE_LIMIT_RPS={_rate_limit_rps} exceeds Hyperliquid's limit of 20 req/sec. "
        "Set to 20.0 or lower to avoid being rate-limited."
    )
if _rate_limit_burst > 20:
    raise ValueError(
        f"RATE_LIMIT_BURST={_rate_limit_burst} exceeds Hyperliquid's limit of 20 req/sec. "
        "Set to 20 or lower."
    )

_global_rate_limiter = RateLimiter(
    requests_per_second=_rate_limit_rps,
    burst_limit=_rate_limit_burst,
    backoff_factor=float(os.getenv("RATE_LIMIT_BACKOFF", "2.0")),
    max_backoff=float(os.getenv("RATE_LIMIT_MAX_BACKOFF", "30.0")),
)

api_wrapper = APICallWrapper(_global_rate_limiter)
