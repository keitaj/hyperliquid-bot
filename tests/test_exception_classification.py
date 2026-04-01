"""Tests for exception classification in APICallWrapper._classify."""

import pytest
from unittest.mock import MagicMock
from hyperliquid.utils.error import Error as HyperliquidAPIError

from exceptions import (
    HyperliquidBotError,
    TransientError,
    RateLimitError,
    NetworkError,
    DataError,
    ConfigurationError,
)
from rate_limiter import APICallWrapper, RateLimiter


def _make_wrapper():
    rl = RateLimiter(requests_per_second=100, burst_limit=100)
    return APICallWrapper(rl)


# ------------------------------------------------------------------ #
#  _classify unit tests
# ------------------------------------------------------------------ #

class TestClassifyRateLimit:

    def test_429_in_message(self):
        result = APICallWrapper._classify(Exception("HTTP 429 Too Many Requests"))
        assert isinstance(result, RateLimitError)

    def test_rate_limit_in_message(self):
        result = APICallWrapper._classify(Exception("rate limit exceeded"))
        assert isinstance(result, RateLimitError)

    def test_hyperliquid_api_429(self):
        result = APICallWrapper._classify(HyperliquidAPIError("429 too many requests"))
        assert isinstance(result, RateLimitError)


class TestClassifyNetwork:

    def test_connection_error(self):
        result = APICallWrapper._classify(ConnectionError("connection refused"))
        assert isinstance(result, NetworkError)

    def test_timeout_error(self):
        result = APICallWrapper._classify(TimeoutError("timed out"))
        assert isinstance(result, NetworkError)

    def test_os_error_timeout(self):
        result = APICallWrapper._classify(OSError("connection timed out"))
        assert isinstance(result, NetworkError)

    def test_os_error_generic(self):
        result = APICallWrapper._classify(OSError("socket error"))
        assert isinstance(result, NetworkError)

    def test_hyperliquid_api_timeout(self):
        result = APICallWrapper._classify(HyperliquidAPIError("request timed out"))
        assert isinstance(result, NetworkError)


class TestClassifyData:

    def test_key_error(self):
        result = APICallWrapper._classify(KeyError("missing_field"))
        assert isinstance(result, DataError)

    def test_type_error(self):
        result = APICallWrapper._classify(TypeError("expected str, got int"))
        assert isinstance(result, DataError)

    def test_hyperliquid_api_generic(self):
        result = APICallWrapper._classify(HyperliquidAPIError("invalid response"))
        assert isinstance(result, DataError)


class TestClassifyConfiguration:

    def test_value_error(self):
        result = APICallWrapper._classify(ValueError("invalid parameter"))
        assert isinstance(result, ConfigurationError)


class TestClassifyChaining:

    def test_original_exception_is_chained(self):
        original = ConnectionError("connection refused")
        result = APICallWrapper._classify(original)
        assert result.__cause__ is original

    def test_unknown_exception_becomes_transient(self):
        result = APICallWrapper._classify(RuntimeError("unexpected"))
        assert isinstance(result, TransientError)


# ------------------------------------------------------------------ #
#  Integration: APICallWrapper.call raises classified exceptions
# ------------------------------------------------------------------ #

class TestCallRaisesClassified:

    def test_network_error_retried_then_raised(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=ConnectionError("refused"))

        with pytest.raises(NetworkError):
            wrapper.call(func)

        assert func.call_count == APICallWrapper.MAX_RETRIES

    def test_rate_limit_retried_then_raised(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=Exception("429 Too Many Requests"))

        with pytest.raises(RateLimitError):
            wrapper.call(func)

        assert func.call_count == APICallWrapper.MAX_RETRIES

    def test_data_error_not_retried(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=KeyError("missing"))

        with pytest.raises(DataError):
            wrapper.call(func)

        assert func.call_count == 1

    def test_config_error_not_retried(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=ValueError("bad param"))

        with pytest.raises(ConfigurationError):
            wrapper.call(func)

        assert func.call_count == 1

    def test_success_returns_value(self):
        wrapper = _make_wrapper()
        func = MagicMock(return_value={"status": "ok"})

        result = wrapper.call(func)

        assert result == {"status": "ok"}

    def test_transient_then_success(self):
        wrapper = _make_wrapper()
        func = MagicMock(side_effect=[ConnectionError("fail"), {"ok": True}])

        result = wrapper.call(func)

        assert result == {"ok": True}
        assert func.call_count == 2


# ------------------------------------------------------------------ #
#  Hierarchy tests
# ------------------------------------------------------------------ #

class TestHierarchy:

    def test_rate_limit_is_transient(self):
        assert issubclass(RateLimitError, TransientError)

    def test_network_is_transient(self):
        assert issubclass(NetworkError, TransientError)

    def test_transient_is_bot_error(self):
        assert issubclass(TransientError, HyperliquidBotError)

    def test_data_is_bot_error(self):
        assert issubclass(DataError, HyperliquidBotError)

    def test_config_is_bot_error(self):
        assert issubclass(ConfigurationError, HyperliquidBotError)

    def test_catch_api_errors_catches_classified(self):
        """Classified exceptions should be caught by API_ERRORS tuple."""
        from rate_limiter import API_ERRORS

        for exc_cls in (RateLimitError, NetworkError, DataError, ConfigurationError):
            try:
                raise exc_cls("test")
            except API_ERRORS:
                pass  # expected
            except Exception:
                pytest.fail(f"{exc_cls.__name__} not caught by API_ERRORS")
