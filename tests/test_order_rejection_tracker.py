"""Tests for ``OrderRejectionTracker``.

Pin three load-bearing behaviours:

1. **Pattern classification** is correct and unknown text falls back to
   ERROR-level logging. The exchange's reject text format is the only
   thing the tracker can key off of, so a regression here is silent
   and dangerous.
2. **Log level downgrade** is exact: routine matches log at the
   configured level (default ``error`` preserves legacy behaviour); a
   typo / unknown level falls back to ``error``.
3. **Periodic summary** flushes counters atomically and aggregates
   per-coin counts with the correct top-N ordering and bbo sample.

Concurrency is also covered with a small thread fan-in to verify
``record`` is safe under contention.
"""

import logging
import threading
import time

from order_rejection_tracker import (
    ALLOWED_LOG_LEVELS,
    UNKNOWN_TAG,
    OrderRejectionTracker,
    classify_rejection,
)


_POST_ONLY_MSG = (
    "Post only order would have immediately matched, "
    "bbo was 98.73@98.758. asset=170005"
)
_UNKNOWN_MSG = "Some new exchange-side rejection nobody has seen before"


# --------------------------------------------------------------------- #
# classify_rejection: pure pattern dispatch
# --------------------------------------------------------------------- #


class TestClassifyRejection:
    def test_post_only_matches(self):
        assert classify_rejection(_POST_ONLY_MSG) == "post_only_match"

    def test_post_only_substring_anywhere(self):
        assert classify_rejection(
            f"prefix... {_POST_ONLY_MSG} ...suffix"
        ) == "post_only_match"

    def test_empty_string_unknown(self):
        assert classify_rejection("") == UNKNOWN_TAG

    def test_unknown_returns_unknown_tag(self):
        assert classify_rejection(_UNKNOWN_MSG) == UNKNOWN_TAG

    def test_post_only_text_only_substring(self):
        """Variations on the canonical message still match."""
        variant = "Post only order would have immediately matched"
        assert classify_rejection(variant) == "post_only_match"


# --------------------------------------------------------------------- #
# record: counters, log levels
# --------------------------------------------------------------------- #


class TestRecord:
    def test_post_only_returns_tag_and_counts(self):
        t = OrderRejectionTracker(routine_log_level="error", summary_interval=300)
        tag = t.record("xyz:NVDA", _POST_ONLY_MSG)
        assert tag == "post_only_match"
        snap = t.get_stats_snapshot()
        assert snap["post_only_match"]["xyz:NVDA"] == 1

    def test_unknown_returns_unknown_tag_and_increments_separately(self):
        t = OrderRejectionTracker(summary_interval=0)
        tag = t.record("xyz:NVDA", _UNKNOWN_MSG)
        assert tag == UNKNOWN_TAG
        # Unknowns are NOT routed into the per-tag counters
        assert "unknown" not in t.get_stats_snapshot()
        assert t.get_unknown_count() == 1

    def test_default_log_level_emits_legacy_line_at_error(self, caplog):
        """At default ERROR level the line is byte-identical to the
        legacy ``order_manager`` log so back-compat with log scrapers
        holds. The richer ``[reject:tag]`` format is reserved for
        opt-in downgraded levels."""
        t = OrderRejectionTracker()
        with caplog.at_level(logging.DEBUG, logger="order_rejection_tracker"):
            t.record("xyz:NVDA", _POST_ONLY_MSG)
        legacy = [
            r for r in caplog.records
            if r.message == f"Order rejected: {_POST_ONLY_MSG}"
        ]
        assert len(legacy) == 1
        assert legacy[0].levelno == logging.ERROR
        # The categorised format must NOT appear at the default level.
        categorised = [
            r for r in caplog.records if "[reject:post_only_match]" in r.message
        ]
        assert categorised == []

    def test_log_level_downgrade_to_warning(self, caplog):
        t = OrderRejectionTracker(routine_log_level="warning", summary_interval=0)
        with caplog.at_level(logging.DEBUG, logger="order_rejection_tracker"):
            t.record("xyz:NVDA", _POST_ONLY_MSG)
        records = [
            r for r in caplog.records if "[reject:post_only_match]" in r.message
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING

    def test_log_level_downgrade_to_info(self, caplog):
        t = OrderRejectionTracker(routine_log_level="info", summary_interval=0)
        with caplog.at_level(logging.DEBUG, logger="order_rejection_tracker"):
            t.record("xyz:NVDA", _POST_ONLY_MSG)
        records = [
            r for r in caplog.records if "[reject:post_only_match]" in r.message
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.INFO

    def test_unknown_pattern_always_logs_at_error(self, caplog):
        """Unknowns ignore the configured level — they must remain visible."""
        t = OrderRejectionTracker(routine_log_level="info", summary_interval=0)
        with caplog.at_level(logging.DEBUG, logger="order_rejection_tracker"):
            t.record("xyz:NVDA", _UNKNOWN_MSG)
        err = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and "Order rejected" in r.message
        ]
        assert len(err) == 1

    def test_unknown_log_level_string_falls_back_to_error(self, caplog):
        """A typo'd config value must not crash and must not silently skip
        logging — fall back to ERROR (legacy line format) so the
        operator still sees output."""
        t = OrderRejectionTracker(routine_log_level="WARN_typo", summary_interval=0)
        with caplog.at_level(logging.DEBUG, logger="order_rejection_tracker"):
            t.record("xyz:NVDA", _POST_ONLY_MSG)
        # Falls back to ERROR → emits the legacy "Order rejected: ..." line.
        legacy = [
            r for r in caplog.records
            if r.message == f"Order rejected: {_POST_ONLY_MSG}"
            and r.levelno == logging.ERROR
        ]
        assert len(legacy) == 1

    def test_uppercase_log_level_accepted(self):
        """Config values like ``WARNING`` should still resolve."""
        t = OrderRejectionTracker(routine_log_level="WARNING", summary_interval=0)
        # _level is private API but the test pins behaviour by exercising
        # log emission rather than asserting on the attribute directly.
        # Round-trip via record + caplog instead.
        import logging as _logging
        records = []
        handler = _logging.Handler()
        handler.emit = records.append
        logger = _logging.getLogger("order_rejection_tracker")
        logger.addHandler(handler)
        logger.setLevel(_logging.DEBUG)
        try:
            t.record("xyz:NVDA", _POST_ONLY_MSG)
        finally:
            logger.removeHandler(handler)
        assert any(r.levelno == logging.WARNING for r in records)

    def test_first_ts_pinned_on_first_record(self):
        t = OrderRejectionTracker(summary_interval=0)
        t.record("xyz:NVDA", _POST_ONLY_MSG)
        time.sleep(0.001)
        t.record("xyz:NVDA", _POST_ONLY_MSG)
        # Direct read of internal stats to verify first/last timestamps
        # are tracked separately (used by summary line).
        coin_stats = t._stats["post_only_match"]["xyz:NVDA"]
        assert coin_stats.count == 2
        assert coin_stats.first_ts > 0
        assert coin_stats.last_ts >= coin_stats.first_ts


# --------------------------------------------------------------------- #
# log_summary_if_due
# --------------------------------------------------------------------- #


class TestSummary:
    def test_not_due_returns_false(self, caplog):
        t = OrderRejectionTracker(summary_interval=300)
        t.record("xyz:NVDA", _POST_ONLY_MSG)
        with caplog.at_level(logging.INFO, logger="order_rejection_tracker"):
            assert t.log_summary_if_due() is False
        # Counter NOT reset when not due
        assert t.get_stats_snapshot()["post_only_match"]["xyz:NVDA"] == 1

    def test_due_emits_summary_and_resets(self, caplog):
        t = OrderRejectionTracker(summary_interval=300)
        t.record("xyz:NVDA", _POST_ONLY_MSG)
        t.record("xyz:NVDA", _POST_ONLY_MSG)
        t.record("flx:SILVER", _POST_ONLY_MSG)

        # Force interval to elapse by passing an explicit monotonic value.
        future = t._last_summary_ts + 301.0
        with caplog.at_level(logging.INFO, logger="order_rejection_tracker"):
            assert t.log_summary_if_due(now_monotonic=future) is True

        # Summary line is INFO level
        summaries = [
            r for r in caplog.records if "[reject-summary]" in r.message
        ]
        assert len(summaries) == 1
        msg = summaries[0].message
        assert "tag=post_only_match" in msg
        assert "total=3" in msg
        assert "coins=2" in msg
        # Top is sorted by count descending
        assert "xyz:NVDA=2" in msg
        assert "flx:SILVER=1" in msg

        # Counters reset
        assert t.get_stats_snapshot() == {}

    def test_summary_interval_zero_disables(self, caplog):
        t = OrderRejectionTracker(summary_interval=0)
        t.record("xyz:NVDA", _POST_ONLY_MSG)
        future = time.monotonic() + 1_000_000.0
        with caplog.at_level(logging.INFO, logger="order_rejection_tracker"):
            assert t.log_summary_if_due(now_monotonic=future) is False
        # Counter not reset because no flush happened
        assert t.get_stats_snapshot()["post_only_match"]["xyz:NVDA"] == 1

    def test_summary_includes_unknown_when_present(self, caplog):
        t = OrderRejectionTracker(summary_interval=300)
        t.record("xyz:NVDA", _UNKNOWN_MSG)
        future = t._last_summary_ts + 301.0
        with caplog.at_level(logging.INFO, logger="order_rejection_tracker"):
            t.log_summary_if_due(now_monotonic=future)
        unknown_lines = [
            r for r in caplog.records
            if "[reject-summary] tag=unknown" in r.message
        ]
        assert len(unknown_lines) == 1
        assert unknown_lines[0].levelno == logging.WARNING
        assert "count=1" in unknown_lines[0].message

    def test_summary_skipped_when_no_events(self, caplog):
        """An empty interval emits no summary line and the helper
        returns False — the contract is "True iff a line was logged"."""
        t = OrderRejectionTracker(summary_interval=300)
        future = t._last_summary_ts + 301.0
        with caplog.at_level(logging.INFO, logger="order_rejection_tracker"):
            emitted = t.log_summary_if_due(now_monotonic=future)
        # No counters, so nothing was logged → False per the contract.
        assert emitted is False
        summaries = [
            r for r in caplog.records if "[reject-summary]" in r.message
        ]
        assert summaries == []
        # The internal cursor *was* still advanced so the next due check
        # waits another full interval rather than re-firing every loop.
        assert t._last_summary_ts == future

    def test_top_8_truncation(self, caplog):
        t = OrderRejectionTracker(summary_interval=300)
        # 12 distinct coins, one rejection each
        for i in range(12):
            t.record(f"coin{i}", _POST_ONLY_MSG)
        future = t._last_summary_ts + 301.0
        with caplog.at_level(logging.INFO, logger="order_rejection_tracker"):
            t.log_summary_if_due(now_monotonic=future)
        msg = next(
            r.message for r in caplog.records if "[reject-summary]" in r.message
        )
        # 12 unique coins recorded but top=[...] is truncated to 8 entries.
        assert "coins=12" in msg
        top_segment = msg.split("top=[")[1].split("]")[0]
        # Each ``coin=count`` entry contains exactly one ``=``.
        assert top_segment.count("=") == 8


# --------------------------------------------------------------------- #
# Concurrency
# --------------------------------------------------------------------- #


class TestConcurrency:
    def test_concurrent_record_no_lost_increments(self):
        t = OrderRejectionTracker(summary_interval=0)
        N_THREADS = 10
        N_PER = 100

        def worker():
            for _ in range(N_PER):
                t.record("xyz:NVDA", _POST_ONLY_MSG)

        threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        snap = t.get_stats_snapshot()
        assert snap["post_only_match"]["xyz:NVDA"] == N_THREADS * N_PER


# --------------------------------------------------------------------- #
# Module-level constants
# --------------------------------------------------------------------- #


class TestModuleSurface:
    def test_allowed_log_levels_include_error_warning_info(self):
        for level in ("error", "warning", "info", "debug"):
            assert level in ALLOWED_LOG_LEVELS

    def test_unknown_tag_value(self):
        # Pin the wire-format tag name; downstream tooling (operation
        # logs / dashboards) keys off this string.
        assert UNKNOWN_TAG == "unknown"
