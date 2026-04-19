"""Tests for AdverseSelectionTracker — post-fill price movement measurement."""

from unittest.mock import MagicMock, patch

from ws.adverse_selection_tracker import AdverseSelectionTracker, FillSnapshot


def _make_market_data(mid_price: float):
    """Create a mock market data manager returning given mid price."""
    md_mgr = MagicMock()
    md = MagicMock()
    md.mid_price = mid_price
    md_mgr.get_market_data.return_value = md
    return md_mgr


class TestOnFill:
    """Tests for fill recording."""

    def test_records_fill_with_mid(self):
        md_mgr = _make_market_data(5200.50)
        tracker = AdverseSelectionTracker(md_mgr, log_interval=9999)

        with patch('ws.adverse_selection_tracker.threading'):
            tracker.on_fill("xyz:SP500", 5200.30, "B")

        assert len(tracker._fills) == 1
        snap = tracker._fills[0]
        assert snap.coin == "xyz:SP500"
        assert snap.fill_px == 5200.30
        assert snap.side == "B"
        assert snap.mid_at_fill == 5200.50

    def test_schedules_three_timers(self):
        md_mgr = _make_market_data(100.0)
        tracker = AdverseSelectionTracker(md_mgr, log_interval=9999)

        with patch('ws.adverse_selection_tracker.threading') as mock_threading:
            mock_timer = MagicMock()
            mock_threading.Timer.return_value = mock_timer
            tracker.on_fill("SP500", 100.0, "B")

        # Should schedule 3 timers (5s, 30s, 60s)
        assert mock_threading.Timer.call_count == 3
        delays = [c[0][0] for c in mock_threading.Timer.call_args_list]
        assert delays == [5, 30, 60]
        assert mock_timer.start.call_count == 3

    def test_skips_when_no_market_data(self):
        md_mgr = MagicMock()
        md_mgr.get_market_data.return_value = None
        tracker = AdverseSelectionTracker(md_mgr)

        with patch('ws.adverse_selection_tracker.threading'):
            tracker.on_fill("SP500", 100.0, "B")

        assert len(tracker._fills) == 0

    def test_skips_when_mid_zero(self):
        md_mgr = _make_market_data(0.0)
        tracker = AdverseSelectionTracker(md_mgr)

        with patch('ws.adverse_selection_tracker.threading'):
            tracker.on_fill("SP500", 100.0, "B")

        assert len(tracker._fills) == 0

    def test_skips_when_stopped(self):
        md_mgr = _make_market_data(100.0)
        tracker = AdverseSelectionTracker(md_mgr)
        tracker._running = False

        with patch('ws.adverse_selection_tracker.threading'):
            tracker.on_fill("SP500", 100.0, "B")

        assert len(tracker._fills) == 0

    def test_fill_count_incremented(self):
        md_mgr = _make_market_data(100.0)
        tracker = AdverseSelectionTracker(md_mgr, log_interval=9999)

        with patch('ws.adverse_selection_tracker.threading'):
            tracker.on_fill("SP500", 100.0, "B")
            tracker.on_fill("SP500", 100.1, "A")
            tracker.on_fill("NVDA", 200.0, "B")

        assert tracker._fill_count["SP500"] == 2
        assert tracker._fill_count["NVDA"] == 1


class TestSampleMid:
    """Tests for delayed mid sampling and adverse selection calculation."""

    def test_buy_adverse_selection(self):
        """BUY fill, mid goes UP → adverse (negative bps)."""
        md_mgr = MagicMock()
        tracker = AdverseSelectionTracker(md_mgr, log_interval=9999)

        snap = FillSnapshot(
            fill_id="test_1", coin="SP500", side="B",
            fill_px=100.0, mid_at_fill=100.0,
            fill_time=0, wall_time=0,
        )

        # Mid moved UP by 2 bps after buy → adverse
        md = MagicMock()
        md.mid_price = 100.02  # +2 bps
        md_mgr.get_market_data.return_value = md

        tracker._sample_mid(snap, "5s")

        assert "5s" in snap.samples
        # direction=-1 * (100.02 - 100.0) / 100.0 * 10000 = -2.0 bps (adverse)
        assert abs(snap.samples["5s"] - (-2.0)) < 0.1

    def test_buy_favorable(self):
        """BUY fill, mid goes DOWN → favorable (positive bps)."""
        md_mgr = MagicMock()
        tracker = AdverseSelectionTracker(md_mgr, log_interval=9999)

        snap = FillSnapshot(
            fill_id="test_2", coin="SP500", side="B",
            fill_px=100.0, mid_at_fill=100.0,
            fill_time=0, wall_time=0,
        )

        md = MagicMock()
        md.mid_price = 99.98  # -2 bps
        md_mgr.get_market_data.return_value = md

        tracker._sample_mid(snap, "5s")

        # direction=-1 * (99.98 - 100.0) / 100.0 * 10000 = +2.0 bps (favorable)
        assert abs(snap.samples["5s"] - 2.0) < 0.1

    def test_sell_adverse_selection(self):
        """SELL fill, mid goes DOWN → adverse (negative bps)."""
        md_mgr = MagicMock()
        tracker = AdverseSelectionTracker(md_mgr, log_interval=9999)

        snap = FillSnapshot(
            fill_id="test_3", coin="SP500", side="A",
            fill_px=100.0, mid_at_fill=100.0,
            fill_time=0, wall_time=0,
        )

        md = MagicMock()
        md.mid_price = 99.97  # -3 bps
        md_mgr.get_market_data.return_value = md

        tracker._sample_mid(snap, "30s")

        # direction=+1 * (99.97 - 100.0) / 100.0 * 10000 = -3.0 bps (adverse)
        assert abs(snap.samples["30s"] - (-3.0)) < 0.1

    def test_sell_favorable(self):
        """SELL fill, mid goes UP → favorable (positive bps)."""
        md_mgr = MagicMock()
        tracker = AdverseSelectionTracker(md_mgr, log_interval=9999)

        snap = FillSnapshot(
            fill_id="test_4", coin="SP500", side="A",
            fill_px=100.0, mid_at_fill=100.0,
            fill_time=0, wall_time=0,
        )

        md = MagicMock()
        md.mid_price = 100.05  # +5 bps
        md_mgr.get_market_data.return_value = md

        tracker._sample_mid(snap, "60s")

        # direction=+1 * (100.05 - 100.0) / 100.0 * 10000 = +5.0 bps (favorable)
        assert abs(snap.samples["60s"] - 5.0) < 0.1

    def test_aggregates_updated(self):
        md_mgr = MagicMock()
        tracker = AdverseSelectionTracker(md_mgr, log_interval=9999)

        snap = FillSnapshot(
            fill_id="test_5", coin="SP500", side="B",
            fill_px=100.0, mid_at_fill=100.0,
            fill_time=0, wall_time=0,
        )

        md = MagicMock()
        md.mid_price = 100.01
        md_mgr.get_market_data.return_value = md

        tracker._sample_mid(snap, "5s")

        assert len(tracker._aggregates["SP500"]["5s"]) == 1

    def test_skips_when_no_market_data(self):
        md_mgr = MagicMock()
        md_mgr.get_market_data.return_value = None
        tracker = AdverseSelectionTracker(md_mgr, log_interval=9999)

        snap = FillSnapshot(
            fill_id="test_6", coin="SP500", side="B",
            fill_px=100.0, mid_at_fill=100.0,
            fill_time=0, wall_time=0,
        )

        tracker._sample_mid(snap, "5s")

        assert "5s" not in snap.samples


class TestSummaryLog:
    """Tests for periodic summary logging."""

    @patch('ws.adverse_selection_tracker.time')
    def test_summary_logged_after_interval(self, mock_time):
        md_mgr = _make_market_data(100.0)
        tracker = AdverseSelectionTracker(md_mgr, log_interval=300)

        mock_time.monotonic.return_value = 0.0
        mock_time.time.return_value = 1000.0
        tracker._last_log_time = 0.0

        # Add some aggregate data
        tracker._aggregates["SP500"]["5s"].append(-1.5)
        tracker._aggregates["SP500"]["5s"].append(-0.5)
        tracker._fill_count["SP500"] = 2

        # Not enough time passed
        mock_time.monotonic.return_value = 100.0
        tracker.maybe_log_summary()
        assert tracker._fill_count["SP500"] == 2  # Not reset

        # Enough time passed
        mock_time.monotonic.return_value = 301.0
        tracker.maybe_log_summary()
        assert tracker._fill_count.get("SP500", 0) == 0  # Reset after log

    def test_summary_resets_aggregates(self):
        md_mgr = _make_market_data(100.0)
        tracker = AdverseSelectionTracker(md_mgr, log_interval=300)

        tracker._aggregates["SP500"]["5s"].extend([-1.0, -2.0])
        tracker._fill_count["SP500"] = 2

        tracker._log_summary()

        assert len(tracker._aggregates) == 0
        assert len(tracker._fill_count) == 0

    def test_no_log_when_empty(self):
        md_mgr = _make_market_data(100.0)
        tracker = AdverseSelectionTracker(md_mgr, log_interval=300)

        # Should not raise or log when no data
        tracker._log_summary()


class TestFillFeedIntegration:
    """Tests for FillFeed → AdverseSelectionTracker notification."""

    def test_fill_feed_notifies_tracker(self):
        from ws.fill_feed import FillFeed

        info = MagicMock()
        tracker_mock = MagicMock()
        order_tracker = MagicMock()
        feed = FillFeed(info, order_tracker, "0x123")
        feed._running = True
        feed.set_adverse_selection_tracker(tracker_mock)

        msg = {
            "data": {
                "isSnapshot": False,
                "fills": [
                    {"coin": "SP500", "px": "100.5", "sz": "0.1", "side": "B", "time": 1234567890},
                ]
            }
        }
        feed._on_fill(msg)

        tracker_mock.on_fill.assert_called_once_with("SP500", 100.5, "B", 1234567890)

    def test_fill_feed_no_tracker_no_error(self):
        from ws.fill_feed import FillFeed

        info = MagicMock()
        order_tracker = MagicMock()
        feed = FillFeed(info, order_tracker, "0x123")
        feed._running = True

        msg = {
            "data": {
                "isSnapshot": False,
                "fills": [
                    {"coin": "SP500", "px": "100.5", "sz": "0.1", "side": "B"},
                ]
            }
        }
        # Should not raise
        feed._on_fill(msg)

    def test_fill_feed_skips_invalid_fills(self):
        from ws.fill_feed import FillFeed

        info = MagicMock()
        tracker_mock = MagicMock()
        order_tracker = MagicMock()
        feed = FillFeed(info, order_tracker, "0x123")
        feed._running = True
        feed.set_adverse_selection_tracker(tracker_mock)

        msg = {
            "data": {
                "isSnapshot": False,
                "fills": [
                    {"coin": "", "px": "0", "sz": "0.1", "side": "B"},  # empty coin
                    {"coin": "SP500", "px": "0", "sz": "0.1", "side": "B"},  # px=0
                ]
            }
        }
        feed._on_fill(msg)

        tracker_mock.on_fill.assert_not_called()


class TestStopAndStats:
    """Tests for lifecycle and observability."""

    def test_stop_logs_final_summary(self):
        md_mgr = _make_market_data(100.0)
        tracker = AdverseSelectionTracker(md_mgr)

        tracker._aggregates["SP500"]["5s"].append(-1.0)
        tracker._fill_count["SP500"] = 1

        tracker.stop()

        assert tracker.is_running is False
        # Aggregates should be cleared by final summary
        assert len(tracker._aggregates) == 0

    def test_stats_property(self):
        md_mgr = _make_market_data(100.0)
        tracker = AdverseSelectionTracker(md_mgr)

        tracker._aggregates["SP500"]["5s"].extend([-1.0, -3.0])
        tracker._fill_count["SP500"] = 2

        stats = tracker.stats
        assert "SP500" in stats
        assert stats["SP500"]["fills"] == 2
        assert abs(stats["SP500"]["avg_5s"] - (-2.0)) < 0.01
