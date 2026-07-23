"""Tests for realized-volatility publishing into the fill-feature log.

Covers the strategy-side wiring added so that ``realized_vol_bps`` reaches
the per-fill feature record: the mid-recording gate opens when fill-feature
logging is on (even with vol-adjust / dynamic-age disabled), and the strategy
publishes the recent realized volatility to the AdverseSelectionTracker each
cycle. Observation-only — must not affect quoting.
"""

from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(publish_vol=False, vol_adjust=False, dynamic_age=False):
    """Minimal MarketMakingStrategy bypassing __init__ (see test_refresh_tolerance)."""
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)
    s.bbo_mode = True
    s.bbo_offset_bps = 0.0
    s._quiet_hours = set()
    s._quiet_spread_multiplier = 0.0
    s._spread_schedule = {}
    s._coin_offset_overrides = {}
    s._coin_spread_overrides = {}
    s._coin_size_overrides = {}
    s._dynamic_offset_enabled = False
    s._adverse_tracker = MagicMock()
    s._publish_vol_for_logging = publish_vol
    s.vol_adjust_enabled = vol_adjust
    s._dynamic_age_enabled = dynamic_age
    s.vol_adjust_multiplier = 2.0
    s.vol_lookback = 30
    s.vol_adjust_max_offset = 50.0
    s._recent_mids = {}
    s._microprice_enabled = False

    md = MagicMock()
    md.price_rounding_params.return_value = (4, True)
    s.market_data = md
    # keep price math trivial so _compute_ideal_prices runs to completion
    s._get_coin_offset = lambda coin: 0.0
    s._calculate_microprice_offsets = lambda coin, off: (off, off)
    s._calculate_inventory_skew = lambda coin, mid: 0.0
    return s, md


def _market_data(mid=100.0, bid=99.99, ask=100.01):
    md = MagicMock()
    md.mid_price = mid
    md.bid = bid
    md.ask = ask
    md.book_imbalance = 0.0
    return md


class TestRecordMidGate:
    def test_records_when_logging_enabled(self):
        """Mid is buffered for logging even with vol-adjust / dynamic-age off."""
        s, _ = _make_strategy(publish_vol=True, vol_adjust=False, dynamic_age=False)
        s._record_mid_price("BTC", 100.0)
        assert "BTC" in s._recent_mids
        assert list(s._recent_mids["BTC"]) == [100.0]

    def test_skipped_when_all_disabled(self):
        """No buffering when logging, vol-adjust and dynamic-age are all off."""
        s, _ = _make_strategy(publish_vol=False, vol_adjust=False, dynamic_age=False)
        s._record_mid_price("BTC", 100.0)
        assert "BTC" not in s._recent_mids


class TestPublishRealizedVol:
    def test_publishes_rv_to_tracker(self):
        """_compute_ideal_prices publishes the computed RV for the coin."""
        s, md = _make_strategy(publish_vol=True)
        md.get_market_data.return_value = _market_data()
        # seed history so _compute_realized_volatility returns a value (>=5)
        for px in (100.0, 100.1, 99.9, 100.2, 99.8):
            s._record_mid_price("BTC", px)

        result = s._compute_ideal_prices("BTC")

        assert result is not None  # price path completed
        s._adverse_tracker.set_coin_volatility.assert_called_once()
        pub_coin, pub_vol = s._adverse_tracker.set_coin_volatility.call_args[0]
        assert pub_coin == "BTC"
        assert pub_vol == s._compute_realized_volatility("BTC")
        assert pub_vol is not None and pub_vol > 0

    def test_no_publish_when_logging_disabled(self):
        """With publishing off, the tracker is not touched (backward compat)."""
        s, md = _make_strategy(publish_vol=False)
        md.get_market_data.return_value = _market_data()
        s._compute_ideal_prices("BTC")
        s._adverse_tracker.set_coin_volatility.assert_not_called()

    def test_no_publish_when_tracker_absent(self):
        """No crash when publishing is on but the tracker was never linked."""
        s, md = _make_strategy(publish_vol=True)
        s._adverse_tracker = None
        md.get_market_data.return_value = _market_data()
        # should not raise
        assert s._compute_ideal_prices("BTC") is not None
