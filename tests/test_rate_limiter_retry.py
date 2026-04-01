"""Unit tests for APICallWrapper automatic retry on 429 errors."""

from unittest.mock import MagicMock
import pytest

from rate_limiter import APICallWrapper, RateLimiter
from exceptions import (
    RateLimitError,
    NetworkError,
    ConfigurationError,
)


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

        with pytest.raises(RateLimitError, match="429"):
            wrapper.call(func)

        assert func.call_count == APICallWrapper.MAX_RETRIES

    def test_non_429_error_raises_immediately(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=ValueError("something broke"))

        with pytest.raises(ConfigurationError, match="something broke"):
            wrapper.call(func)

        assert func.call_count == 1

    def test_non_retriable_error_not_retried(self):
        """Non-rate-limit, non-timeout errors should propagate without retry."""
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=[ValueError("bad data"), "ok"])

        with pytest.raises(ConfigurationError):
            wrapper.call(func)

        assert func.call_count == 1

    def test_timeout_error_retried(self):
        """Timeout errors should trigger retry."""
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=[ConnectionError("Connection aborted, timeout"), "ok"])

        result = wrapper.call(func)
        assert result == "ok"
        assert func.call_count == 2

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


class TestClassify:

    def test_429_classified_as_rate_limit(self):
        result = APICallWrapper._classify(Exception("429 Too Many Requests"))
        assert isinstance(result, RateLimitError)

    def test_rate_limit_message_classified(self):
        result = APICallWrapper._classify(Exception("Rate Limit Exceeded"))
        assert isinstance(result, RateLimitError)

    def test_rate_limit_lowercase_classified(self):
        result = APICallWrapper._classify(Exception("rate limit"))
        assert isinstance(result, RateLimitError)

    def test_connection_timeout_classified_as_network(self):
        result = APICallWrapper._classify(ConnectionError("connection timeout"))
        assert isinstance(result, NetworkError)

    def test_empty_message_not_rate_limit(self):
        result = APICallWrapper._classify(Exception(""))
        assert not isinstance(result, RateLimitError)
