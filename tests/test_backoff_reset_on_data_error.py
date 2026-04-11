"""Test that non-transient errors reset rate limiter backoff."""

from rate_limiter import APICallWrapper, RateLimiter
from exceptions import DataError


def _make_wrapper():
    rl = RateLimiter(requests_per_second=10, burst_limit=20)
    wrapper = APICallWrapper(rl)
    return wrapper, rl


class TestBackoffResetOnNonTransientError:
    """Non-transient errors should reset backoff to avoid penalising
    subsequent unrelated API calls."""

    def test_backoff_reset_after_key_error(self):
        """KeyError is classified as DataError → backoff should reset."""
        wrapper, rl = _make_wrapper()

        # Simulate a 429 that sets backoff
        rl.on_429_error()
        assert rl._current_backoff > 0

        # KeyError → classified as DataError → non-transient → backoff reset
        def failing_func():
            raise KeyError("missing field")

        try:
            wrapper.call(failing_func)
        except DataError:
            pass

        assert rl._current_backoff == 0
        assert rl._consecutive_429s == 0

    def test_backoff_reset_after_value_error(self):
        """ValueError is classified as ConfigurationError → backoff should reset."""
        wrapper, rl = _make_wrapper()

        rl.on_429_error()
        rl.on_429_error()
        assert rl._current_backoff > 0

        def failing_func():
            raise ValueError("bad config")

        try:
            wrapper.call(failing_func)
        except Exception:
            pass

        assert rl._current_backoff == 0

    def test_successful_call_after_data_error_works_normally(self):
        wrapper, rl = _make_wrapper()

        rl.on_429_error()
        rl.on_429_error()
        assert rl._current_backoff > 0

        def failing_func():
            raise KeyError("bad data")

        try:
            wrapper.call(failing_func)
        except DataError:
            pass

        assert rl._current_backoff == 0

        # Next call should work without delay
        result = wrapper.call(lambda: "ok")
        assert result == "ok"


class TestResetBackoffMethod:
    """RateLimiter.reset_backoff() clears state independently of on_success()."""

    def test_reset_clears_backoff(self):
        rl = RateLimiter(requests_per_second=10, burst_limit=20)
        rl.on_429_error()
        rl.on_429_error()
        assert rl._current_backoff > 0
        assert rl._consecutive_429s == 2

        rl.reset_backoff()
        assert rl._current_backoff == 0
        assert rl._consecutive_429s == 0

    def test_reset_noop_when_no_backoff(self):
        rl = RateLimiter(requests_per_second=10, burst_limit=20)
        rl.reset_backoff()  # Should not raise
        assert rl._current_backoff == 0
