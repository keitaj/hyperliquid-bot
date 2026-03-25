"""Unit tests for APICallWrapper automatic retry on 429 errors."""

from unittest.mock import MagicMock, patch
import pytest

from rate_limiter import APICallWrapper, RateLimiter


def _make_wrapper(max_backoff=1.0):
    """Create an APICallWrapper with a fast rate limiter for testing."""
    rl = RateLimiter(requests_per_second=100, burst_limit=100, backoff_factor=2.0, max_backoff=max_backoff)
    return APICallWrapper(rl)


class TestRetryOn429:

    def test_success_on_first_attempt(self):
        wrapper = _make_wrapper()
        func = MagicMock(return_value="ok")

        result = wrapper.call(func)

        assert result == "ok"
        assert func.call_count == 1

    def test_retries_on_429_then_succeeds(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=[Exception("429 Too Many Requests"), "ok"])

        result = wrapper.call(func)

        assert result == "ok"
        assert func.call_count == 2

    def test_retries_on_rate_limit_message(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=[Exception("rate limit exceeded"), "ok"])

        result = wrapper.call(func)

        assert result == "ok"
        assert func.call_count == 2

    def test_gives_up_after_max_retries(self):
        wrapper = _make_wrapper()
        error = Exception("429 Too Many Requests")
        func = MagicMock(side_effect=error)

        with pytest.raises(Exception, match="429"):
            wrapper.call(func)

        assert func.call_count == APICallWrapper.MAX_RETRIES

    def test_non_429_error_raises_immediately(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=ValueError("something broke"))

        with pytest.raises(ValueError, match="something broke"):
            wrapper.call(func)

        assert func.call_count == 1

    def test_non_429_error_not_retried(self):
        """Non-rate-limit errors should propagate without retry."""
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=[ConnectionError("timeout"), "ok"])

        with pytest.raises(ConnectionError):
            wrapper.call(func)

        assert func.call_count == 1

    def test_backoff_increases_on_consecutive_429s(self):
        wrapper = _make_wrapper(max_backoff=60.0)
        error = Exception("429")
        func = MagicMock(side_effect=error)

        with pytest.raises(Exception):
            wrapper.call(func)

        # After MAX_RETRIES 429 errors, backoff should have increased
        assert wrapper.rate_limiter._consecutive_429s == APICallWrapper.MAX_RETRIES
        assert wrapper.rate_limiter._current_backoff > 0

    def test_backoff_resets_on_success_after_retry(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=[Exception("429"), "ok"])

        wrapper.call(func)

        assert wrapper.rate_limiter._consecutive_429s == 0
        assert wrapper.rate_limiter._current_backoff == 0.0

    def test_retry_with_args_and_kwargs(self):
        """Arguments should be passed through on every retry attempt."""
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=[Exception("429"), "ok"])

        result = wrapper.call(func, "arg1", "arg2", key="val")

        assert result == "ok"
        for c in func.call_args_list:
            assert c.args == ("arg1", "arg2")
            assert c.kwargs == {"key": "val"}


class TestIsRateLimitError:

    def test_429_in_message(self):
        assert APICallWrapper._is_rate_limit_error(Exception("429 Too Many Requests"))

    def test_rate_limit_in_message(self):
        assert APICallWrapper._is_rate_limit_error(Exception("Rate Limit Exceeded"))

    def test_rate_limit_lowercase(self):
        assert APICallWrapper._is_rate_limit_error(Exception("rate limit"))

    def test_unrelated_error(self):
        assert not APICallWrapper._is_rate_limit_error(Exception("connection timeout"))

    def test_empty_message(self):
        assert not APICallWrapper._is_rate_limit_error(Exception(""))
