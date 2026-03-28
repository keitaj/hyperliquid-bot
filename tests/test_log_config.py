"""Tests for log_config: setup_logging and JSONFormatter."""

import json
import logging
import os
from unittest.mock import patch

from log_config import setup_logging, JSONFormatter


class TestSetupLogging:

    def teardown_method(self):
        """Reset root logger after each test."""
        root = logging.getLogger()
        for h in root.handlers[:]:
            root.removeHandler(h)

    def test_default_is_text_format(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOG_FORMAT", None)
            setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert not isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_json_format(self):
        with patch.dict(os.environ, {"LOG_FORMAT": "json"}):
            setup_logging()
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_log_level_override(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            setup_logging()
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_log_level_default_is_info(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOG_LEVEL", None)
            setup_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO


class TestJSONFormatter:

    def test_basic_message(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test"
        assert "timestamp" in parsed

    def test_exception_included(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="something broke", args=(), exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["exception"]["type"] == "ValueError"
        assert "test error" in parsed["exception"]["message"]
        assert len(parsed["exception"]["traceback"]) > 0

    def test_extra_fields_propagated(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="order placed", args=(), exc_info=None,
        )
        record.coin = "BTC"
        record.side = "buy"
        record.price = 50000.0
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["coin"] == "BTC"
        assert parsed["side"] == "buy"
        assert parsed["price"] == 50000.0

    def test_missing_extra_fields_not_included(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="simple message", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "coin" not in parsed
        assert "side" not in parsed

    def test_output_is_single_line(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="line1\nline2", args=(), exc_info=None,
        )
        output = formatter.format(record)
        # json.dumps produces single-line by default (newlines escaped)
        assert "\n" not in output
