"""Tests for ws.fill_feature_writer.FillFeatureWriter — buffered JSONL output."""

import json
import os
from unittest.mock import patch

from ws.adverse_selection_tracker import FillSnapshot
from ws.fill_feature_writer import MATURITY_SECONDS, FillFeatureWriter


def _make_snapshot(
    fill_time: float,
    record=None,
    samples=None,
    coin: str = "xyz:SP500",
) -> FillSnapshot:
    snap = FillSnapshot(
        fill_id=f"{coin}_{fill_time:.3f}",
        coin=coin,
        side="B",
        fill_px=100.0,
        mid_at_fill=100.0,
        fill_time=fill_time,
        wall_time=1751900000.0,
    )
    snap.record = record if record is not None else {"v": 1, "coin": coin}
    if samples:
        snap.samples.update(samples)
    return snap


def _read_lines(path: str):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestFlushMaturity:
    def test_immature_record_not_flushed(self, tmp_path):
        writer = FillFeatureWriter(str(tmp_path), flush_interval=0.0)
        writer._last_flush = 0.0
        with patch("ws.fill_feature_writer.time.monotonic", return_value=1000.0):
            writer.add(_make_snapshot(fill_time=1000.0))
            writer.maybe_flush()

        assert writer.stats["written"] == 0
        assert writer.stats["buffered"] == 1
        assert not os.listdir(tmp_path)

    def test_mature_record_flushed_with_labels(self, tmp_path):
        writer = FillFeatureWriter(str(tmp_path), flush_interval=0.0)
        writer._last_flush = 0.0
        snap = _make_snapshot(
            fill_time=1000.0,
            record={"v": 1, "coin": "xyz:SP500", "px": 100.0},
            samples={"5s": -1.2, "30s": -2.4},  # 60s sample missing -> null
        )
        writer.add(snap)
        with patch(
            "ws.fill_feature_writer.time.monotonic",
            return_value=1000.0 + MATURITY_SECONDS + 1,
        ):
            writer.maybe_flush()

        files = os.listdir(tmp_path)
        assert len(files) == 1
        rows = _read_lines(os.path.join(tmp_path, files[0]))
        assert len(rows) == 1
        row = rows[0]
        assert row["coin"] == "xyz:SP500"
        assert row["mo_5s"] == -1.2
        assert row["mo_30s"] == -2.4
        assert row["mo_60s"] is None
        assert writer.stats["written"] == 1
        assert writer.stats["buffered"] == 0

    def test_flush_stops_at_first_immature(self, tmp_path):
        """Chronological buffer: mature head flushed, immature tail kept."""
        writer = FillFeatureWriter(str(tmp_path), flush_interval=0.0)
        writer._last_flush = 0.0
        writer.add(_make_snapshot(fill_time=1000.0))
        writer.add(_make_snapshot(fill_time=2000.0))
        with patch(
            "ws.fill_feature_writer.time.monotonic",
            return_value=1000.0 + MATURITY_SECONDS + 1,
        ):
            writer.maybe_flush()

        assert writer.stats["written"] == 1
        assert writer.stats["buffered"] == 1

    def test_flush_all_writes_immature_with_null_labels(self, tmp_path):
        writer = FillFeatureWriter(str(tmp_path))
        writer._last_flush = 0.0
        with patch("ws.fill_feature_writer.time.monotonic", return_value=1000.0):
            writer.add(_make_snapshot(fill_time=1000.0))
            writer.flush_all()

        files = os.listdir(tmp_path)
        assert len(files) == 1
        rows = _read_lines(os.path.join(tmp_path, files[0]))
        assert rows[0]["mo_5s"] is None
        assert rows[0]["mo_30s"] is None
        assert rows[0]["mo_60s"] is None


class TestFlushInterval:
    def test_interval_gates_io(self, tmp_path):
        writer = FillFeatureWriter(str(tmp_path), flush_interval=60.0)
        writer._last_flush = 0.0
        base = 5000.0
        with patch("ws.fill_feature_writer.time.monotonic", return_value=base):
            writer._last_flush = base
            writer.add(_make_snapshot(fill_time=base - MATURITY_SECONDS - 10))
            writer.maybe_flush()  # interval not elapsed -> no IO
        assert writer.stats["written"] == 0

        with patch(
            "ws.fill_feature_writer.time.monotonic", return_value=base + 61.0
        ):
            writer.maybe_flush()
        assert writer.stats["written"] == 1


class TestRotationAndCaps:
    def test_daily_rotation_by_utc_date(self, tmp_path):
        # Exercise the REAL _current_path (UTC date -> filename) by driving
        # the module clock, so a regression to local time is caught.
        from datetime import datetime, timezone

        writer = FillFeatureWriter(str(tmp_path), flush_interval=0.0)
        writer._last_flush = 0.0

        class _FakeDateTime:
            _now = datetime(2026, 7, 8, 23, 59, 0, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls._now

        with patch("ws.fill_feature_writer.datetime", _FakeDateTime):
            with patch(
                "ws.fill_feature_writer.time.monotonic", return_value=10_000.0
            ):
                writer.add(_make_snapshot(fill_time=1000.0))
                writer.maybe_flush()
                # Advance one UTC day (also crosses local midnight the other way
                # for negative-offset zones, so file date must track UTC).
                _FakeDateTime._now = datetime(2026, 7, 9, 0, 1, 0, tzinfo=timezone.utc)
                writer.add(_make_snapshot(fill_time=2000.0))
                writer.maybe_flush()

        assert sorted(os.listdir(tmp_path)) == ["20260708.jsonl", "20260709.jsonl"]

    def test_current_path_uses_utc_not_local(self):
        # An instant where local date differs from UTC date: file date
        # must be the UTC one regardless of the host timezone.
        from datetime import datetime, timezone

        writer = FillFeatureWriter.__new__(FillFeatureWriter)
        writer.log_dir = "/tmp/x"

        class _FakeDateTime:
            @staticmethod
            def now(tz=None):
                # 23:30 UTC on the 8th; local (UTC+2) would be the 9th
                assert tz == timezone.utc
                return datetime(2026, 7, 8, 23, 30, tzinfo=timezone.utc)

        with patch("ws.fill_feature_writer.datetime", _FakeDateTime):
            assert writer._current_path().endswith("20260708.jsonl")

    def test_daily_size_cap_drops_records(self, tmp_path):
        writer = FillFeatureWriter(str(tmp_path), flush_interval=0.0, max_daily_bytes=10)
        writer._last_flush = 0.0
        path = writer._current_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * 100)  # already over the 10-byte cap

        with patch(
            "ws.fill_feature_writer.time.monotonic", return_value=10_000.0
        ):
            writer.add(_make_snapshot(fill_time=1000.0))
            writer.maybe_flush()

        assert writer.stats["written"] == 0
        assert writer.stats["dropped_size_cap"] == 1
        assert os.path.getsize(path) == 100  # nothing appended

    def test_buffer_overflow_drops_oldest(self, tmp_path):
        writer = FillFeatureWriter(str(tmp_path), buffer_max=2)
        writer._last_flush = 0.0
        writer.add(_make_snapshot(fill_time=1.0))
        writer.add(_make_snapshot(fill_time=2.0))
        writer.add(_make_snapshot(fill_time=3.0))

        assert writer.stats["buffered"] == 2
        assert writer.stats["dropped_overflow"] == 1
        # The oldest (fill_time=1.0) must be the one evicted, not the newest.
        remaining = [snap.fill_time for snap in writer._buffer]
        assert remaining == [2.0, 3.0]


class TestFailSilent:
    def test_io_error_does_not_raise(self, tmp_path):
        writer = FillFeatureWriter(str(tmp_path), flush_interval=0.0)
        writer._last_flush = 0.0
        writer.add(_make_snapshot(fill_time=1000.0))
        with patch(
            "ws.fill_feature_writer.time.monotonic", return_value=10_000.0
        ):
            with patch("builtins.open", side_effect=OSError("disk full")):
                writer.maybe_flush()  # must not raise

        assert writer.stats["errors"] == 1

    def test_io_error_rebuffers_records_for_retry(self, tmp_path):
        # A transient write failure must NOT lose the good records — they
        # stay buffered and the next flush writes them.
        writer = FillFeatureWriter(str(tmp_path), flush_interval=0.0)
        writer._last_flush = 0.0
        writer.add(_make_snapshot(fill_time=1000.0))
        writer.add(_make_snapshot(fill_time=1001.0))
        with patch(
            "ws.fill_feature_writer.time.monotonic", return_value=10_000.0
        ):
            with patch("builtins.open", side_effect=OSError("disk full")):
                writer.maybe_flush()
            assert writer.stats["written"] == 0
            assert writer.stats["buffered"] == 2  # re-buffered, not lost
            # Order preserved for the retry
            assert [s.fill_time for s in writer._buffer] == [1000.0, 1001.0]
            # Next flush succeeds
            writer.maybe_flush()

        rows = _read_lines(os.path.join(tmp_path, os.listdir(tmp_path)[0]))
        assert len(rows) == 2
        assert writer.stats["written"] == 2
        assert writer.stats["buffered"] == 0

    def test_one_bad_record_does_not_sink_batch(self, tmp_path):
        # [good, bad, good] must write the two good rows and drop only the bad.
        writer = FillFeatureWriter(str(tmp_path), flush_interval=0.0)
        writer._last_flush = 0.0
        writer.add(_make_snapshot(fill_time=1000.0, record={"v": 1, "id": "a"}))
        writer.add(_make_snapshot(fill_time=1001.0, record={"bad": object()}))
        writer.add(_make_snapshot(fill_time=1002.0, record={"v": 1, "id": "c"}))
        with patch(
            "ws.fill_feature_writer.time.monotonic", return_value=10_000.0
        ):
            writer.maybe_flush()

        rows = _read_lines(os.path.join(tmp_path, os.listdir(tmp_path)[0]))
        assert [r["id"] for r in rows] == ["a", "c"]
        assert writer.stats["written"] == 2
        assert writer.stats["dropped_serialize"] == 1
        assert writer.stats["buffered"] == 0

    def test_makedirs_failure_disables_writer(self, tmp_path):
        with patch("ws.fill_feature_writer.os.makedirs", side_effect=OSError("denied")):
            writer = FillFeatureWriter(str(tmp_path / "sub"))
            writer._last_flush = 0.0

        assert writer.stats["enabled"] is False
        # All operations become silent no-ops
        writer.add(_make_snapshot(fill_time=1000.0))
        writer.maybe_flush()
        writer.flush_all()
        assert writer.stats["written"] == 0

    def test_unserializable_record_counted_not_raised(self, tmp_path):
        writer = FillFeatureWriter(str(tmp_path), flush_interval=0.0)
        writer._last_flush = 0.0
        snap = _make_snapshot(fill_time=1000.0, record={"bad": object()})
        writer.add(snap)
        with patch(
            "ws.fill_feature_writer.time.monotonic", return_value=10_000.0
        ):
            writer.maybe_flush()  # json.dumps fails -> swallowed

        assert writer.stats["errors"] == 1
        assert writer.stats["dropped_serialize"] == 1
        assert writer.stats["buffered"] == 0  # poison discarded, not retried
