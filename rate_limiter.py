import time
import logging
from typing import Dict, Optional
from threading import Lock
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class RateLimiter:
    """Rate limiter to prevent API rate limit errors"""
    
    def __init__(self, 
                 requests_per_second: float = 2.0,
                 burst_limit: int = 5,
                 backoff_factor: float = 2.0,
                 max_backoff: float = 60.0):
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
    """Wrapper to add rate limiting to API calls"""
    
    def __init__(self, rate_limiter: RateLimiter):
        self.rate_limiter = rate_limiter
    
    def call(self, func, *args, **kwargs):
        """Execute API call with rate limiting and error handling"""
        self.rate_limiter.wait_if_needed()
        
        try:
            result = func(*args, **kwargs)
            self.rate_limiter.on_success()
            return result
        except Exception as e:
            # Check if it's a 429 error (rate limit)
            error_str = str(e)
            if "429" in error_str or "rate limit" in error_str.lower():
                self.rate_limiter.on_429_error()
                # Wait before retrying
                time.sleep(self.rate_limiter._current_backoff)
            raise e


# Global rate limiter instance
_global_rate_limiter = RateLimiter(
    requests_per_second=1.5,  # Conservative rate
    burst_limit=3,            # Low burst limit
    backoff_factor=2.0,
    max_backoff=30.0
)

api_wrapper = APICallWrapper(_global_rate_limiter)