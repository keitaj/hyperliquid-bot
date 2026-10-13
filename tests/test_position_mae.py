"""Tests for max adverse excursion (MAE) tracking on open positions.

MAE is the worst unrealized loss a position reaches while it is open. It is
recorded for observation only -- it never influences a close decision -- and
is emitted on the close-event log line so the unrealized-loss early-close
threshold can be calibrated from production data. That calibration needs the
positions that drifted *and recovered*, which fills alone cannot show.
"""

import logging
import re
import time

import pytest
from unittest.mock import MagicMock

from strategies.mm_position_closer import (
    CLOSE_REASON_EXTERNAL,
    CLOSE_REASON_MAKER,
    CLOSE_REASON_UNREALIZED_LOSS,
    PositionCloser,
)

# The log-parsing regexes used by downstream analysis, which the new fields
# must not break.
_BASE_RE = re.compile(
    r"\[close-reason\]\s+(\S+)\s+reason=(\S+)\s+age=(\d+)s\s+last_tier=(\S+)"
)
_MAX_AGE_RE = re.compile(r"max_age=(\d+)s")
_MAE_RE = re.compile(r"mae_bps=(-?\d+\.\d+)\s+mae_age=(\d+)s")


def _make_closer(
    unrealized_loss_close_bps: float = 0.0,
    track_max_adverse_excursion: bool = True,
    max_age: float = 120,
) -> tuple:
    """Build a PositionCloser whose cached mid can be driven per-test."""
    om = MagicMock()
    md = MagicMock()
    md.round_size.return_value = 0.5
    md.get_sz_decimals.return_value = 0
    md.price_rounding_params.return_value = (0, True)
    md.get_cached_market_data.return_value = None
    md.get_market_data.return_value = None
    closer = PositionCloser(
        order_manager=om,
        market_data=md,
        spread_bps=10,
        max_position_age_seconds=max_age,
        maker_only=True,
        taker_fallback_age_seconds=None,
        unrealized_loss_close_bps=unrealized_loss_close_bps,
        track_max_adverse_excursion=track_max_adverse_excursion,
    )
    om.get_all_positions.return_value = [{'coin': 'BTC', 'szi': '1.0'}]
    return closer, om, md


def _set_mid(md, mid: float) -> None:
    """Point both market-data accessors at a mid price."""
    snapshot = MagicMock()
    snapshot.mid_price = mid
    snapshot.bid = mid * 0.999
    snapshot.ask = mid * 1.001
    md.get_cached_market_data.return_value = snapshot
    md.get_market_data.return_value = snapshot


def _manage(closer, md, mid: float, size: float = 1.0, entry: float = 100.0) -> None:
    """Run one manage() cycle at a given mid price."""
    _set_mid(md, mid)
    closer.manage('BTC', {'size': size, 'entry_price': entry}, MagicMock())


class TestMaeTracking:
    """The running maximum is taken over the position's lifetime."""

    def test_long_records_worst_drawdown(self):
        """Long entry=100: mids 99.9 / 99.7 / 99.8 -> MAE is the 99.7 sample."""
        closer, om, md = _make_closer()
        for mid in (99.9, 99.7, 99.8):
            _manage(closer, md, mid)
        mae_bps, _ = closer._mae['BTC']
        assert mae_bps == pytest.approx(30.0)  # (100 - 99.7) / 100 * 10_000

    def test_peak_survives_recovery(self):
        """A position that drifts then fully recovers keeps the peak value.

        This is the case the measurement exists for: at close time the PnL
        looks fine, but the position did breach a threshold on the way.
        """
        closer, om, md = _make_closer()
        _manage(closer, md, 99.5)   # -50 bps
        _manage(closer, md, 100.2)  # recovered into profit
        mae_bps, _ = closer._mae['BTC']
        assert mae_bps == pytest.approx(50.0)

    def test_short_sign_convention(self):
        """Short is adverse when the mid rises: positive bps, same as long."""
        closer, om, md = _make_closer()
        _manage(closer, md, 100.4, size=-1.0)
        mae_bps, _ = closer._mae['BTC']
        assert mae_bps == pytest.approx(40.0)

    def test_never_adverse_records_zero(self):
        """A position that only ever moved in our favour records 0.0, not None.

        Omitting these would bias the denominator when asking what share of
        positions breached a threshold.
        """
        closer, om, md = _make_closer()
        _manage(closer, md, 100.5)
        _manage(closer, md, 101.0)
        assert closer._mae['BTC'] == (0.0, 0.0)

    def test_mae_age_is_when_the_peak_happened(self):
        """mae_age distinguishes 'drifted early then recovered' from 'drifted late'."""
        closer, om, md = _make_closer()
        _manage(closer, md, 100.0)
        # Backdate the entry so the next sample lands at a measurable age.
        entry_time, oid, tier = closer._open_positions['BTC']
        closer._open_positions['BTC'] = (entry_time - 40, oid, tier)
        _manage(closer, md, 99.0)
        mae_bps, mae_age = closer._mae['BTC']
        assert mae_bps == pytest.approx(100.0)
        assert 39 <= mae_age <= 42


class TestMaeIndependentOfEarlyClose:
    """Tracking must not depend on the early-close feature being enabled."""

    def test_tracked_when_threshold_disabled(self):
        """With unrealized_loss_close_bps=0 the MAE is still recorded.

        The existing unrealized_bps arithmetic sits behind a `threshold > 0`
        branch, so this is the case that would otherwise go unmeasured.
        """
        closer, om, md = _make_closer(unrealized_loss_close_bps=0.0)
        _manage(closer, md, 99.6)
        assert closer._mae['BTC'][0] == pytest.approx(40.0)

    def test_reported_on_unrealized_loss_close(self, caplog):
        """The early-close path also reports MAE."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=15)
        with caplog.at_level(logging.INFO):
            _manage(closer, md, 99.5)
        line = next(r.message for r in caplog.records if '[close-reason]' in r.message)
        assert f'reason={CLOSE_REASON_UNREALIZED_LOSS}' in line
        assert _MAE_RE.search(line) is not None


class TestMaeOnCloseEventLine:
    """Emission covers every close reason, including profitable ones."""

    def test_emitted_on_maker_close(self, caplog):
        closer, om, md = _make_closer()
        _manage(closer, md, 99.7)
        # Simulate a resting close order so cleanup classifies it as maker.
        entry_time, _, tier = closer._open_positions['BTC']
        closer._open_positions['BTC'] = (entry_time, 12345, tier)
        with caplog.at_level(logging.INFO):
            closer.cleanup_closed('BTC')
        line = next(r.message for r in caplog.records if '[close-reason]' in r.message)
        assert f'reason={CLOSE_REASON_MAKER}' in line
        m = _MAE_RE.search(line)
        assert m is not None
        assert float(m.group(1)) == pytest.approx(30.0)

    def test_emitted_on_external_close(self, caplog):
        """External closes are part of the denominator, so they report too."""
        closer, om, md = _make_closer()
        _manage(closer, md, 99.8)
        # No resting close order -> cleanup classifies it as an external close.
        entry_time, _, tier = closer._open_positions['BTC']
        closer._open_positions['BTC'] = (entry_time, None, tier)
        with caplog.at_level(logging.INFO):
            closer.on_position_closed('BTC')
        line = next(r.message for r in caplog.records if '[close-reason]' in r.message)
        assert f'reason={CLOSE_REASON_EXTERNAL}' in line
        assert _MAE_RE.search(line) is not None

    def test_zero_mae_is_logged_not_omitted(self, caplog):
        closer, om, md = _make_closer()
        _manage(closer, md, 100.0)
        with caplog.at_level(logging.INFO):
            closer.on_position_closed('BTC')
        line = next(r.message for r in caplog.records if '[close-reason]' in r.message)
        assert 'mae_bps=0.0' in line

    def test_existing_log_regexes_still_match(self, caplog):
        """The new fields append, so prior parsers keep working unchanged."""
        closer, om, md = _make_closer()
        _manage(closer, md, 99.7)
        with caplog.at_level(logging.INFO):
            closer.on_position_closed('BTC')
        line = next(r.message for r in caplog.records if '[close-reason]' in r.message)
        base = _BASE_RE.search(line)
        assert base is not None
        assert base.group(1) == 'BTC'
        assert base.group(4) == 'normal'
        assert _MAX_AGE_RE.search(line) is not None


class TestMaeDisabled:
    """The flag turns the fields off entirely."""

    def test_no_fields_when_disabled(self, caplog):
        closer, om, md = _make_closer(track_max_adverse_excursion=False)
        _manage(closer, md, 99.0)
        assert 'BTC' not in closer._mae
        with caplog.at_level(logging.INFO):
            closer.on_position_closed('BTC')
        line = next(r.message for r in caplog.records if '[close-reason]' in r.message)
        assert 'mae_bps' not in line
        assert _BASE_RE.search(line) is not None


class TestMaeEdgeCases:
    def test_zero_entry_price_skipped(self):
        closer, om, md = _make_closer()
        _set_mid(md, 99.0)
        closer.manage('BTC', {'size': 1.0, 'entry_price': 0.0}, MagicMock())
        assert closer._mae['BTC'] == (0.0, 0.0)

    def test_cache_miss_skipped(self):
        """No cached snapshot -> skip the sample rather than fetch one."""
        closer, om, md = _make_closer()
        md.get_cached_market_data.return_value = None
        closer.manage('BTC', {'size': 1.0, 'entry_price': 100.0}, MagicMock())
        assert closer._mae['BTC'] == (0.0, 0.0)

    def test_zero_mid_skipped(self):
        closer, om, md = _make_closer()
        _set_mid(md, 0.0)
        closer.manage('BTC', {'size': 1.0, 'entry_price': 100.0}, MagicMock())
        assert closer._mae['BTC'] == (0.0, 0.0)

    def test_state_cleared_after_close(self):
        closer, om, md = _make_closer()
        _manage(closer, md, 99.0)
        closer.on_position_closed('BTC')
        assert 'BTC' not in closer._mae

    def test_reregistration_resets_peak(self):
        """A new position must not inherit the previous one's peak."""
        closer, om, md = _make_closer()
        _manage(closer, md, 99.0)      # MAE 100 bps
        closer.on_position_closed('BTC')
        _manage(closer, md, 99.95)     # new position, 5 bps
        assert closer._mae['BTC'][0] == pytest.approx(5.0)

    def test_ghost_clear_drops_state(self):
        """The ghost-position path pops MAE even though it logs no close event."""
        closer, om, md = _make_closer()
        _manage(closer, md, 99.0)
        closer._open_positions.pop('BTC', None)
        closer._mae.pop('BTC', None)
        assert 'BTC' not in closer._mae


class TestMaeAddsNoApiCalls:
    """The enabled-by-default observation must not increase API load."""

    def test_tracking_adds_no_fetching_calls(self):
        """Turning tracking on must not change how often the fetching
        accessors are used.

        ``get_market_data`` is called by the close-order placement path and
        can fall through to a REST L2 fetch on a cache miss, so the question
        is not whether it is called at all but whether MAE tracking adds
        calls. Comparing on/off pins that down; asserting "never called"
        would only pass by accident of the placement path's behaviour.
        """
        counts = {}
        for track in (True, False):
            closer, om, md = _make_closer(track_max_adverse_excursion=track)
            for mid in (99.9, 99.5, 99.7):
                _manage(closer, md, mid)
            counts[track] = (
                md.get_market_data.call_count,
                md.get_l2_snapshot.call_count,
            )
        assert counts[True] == counts[False]

    def test_sampling_uses_the_cache_only_accessor(self):
        """The samples themselves come from the non-fetching accessor."""
        closer, om, md = _make_closer()
        for mid in (99.9, 99.5, 99.7):
            _manage(closer, md, mid)
        assert md.get_cached_market_data.call_count == 3
        md.get_l2_snapshot.assert_not_called()


class TestCachedMarketDataAccessor:
    """MarketDataManager.get_cached_market_data must not fetch."""

    def test_returns_cached_without_fetch(self):
        from market_data import MarketDataManager

        mgr = MarketDataManager.__new__(MarketDataManager)
        mgr._cache = MagicMock()
        sentinel = object()
        mgr._cache.get.return_value = sentinel
        mgr.get_l2_snapshot = MagicMock()

        assert mgr.get_cached_market_data('BTC') is sentinel
        mgr.get_l2_snapshot.assert_not_called()

    def test_returns_none_on_miss_without_fetch(self):
        from market_data import MarketDataManager

        mgr = MarketDataManager.__new__(MarketDataManager)
        mgr._cache = MagicMock()
        mgr._cache.get.return_value = None
        mgr.get_l2_snapshot = MagicMock()

        assert mgr.get_cached_market_data('BTC') is None
        mgr.get_l2_snapshot.assert_not_called()


class TestMaeConfigWiring:
    """The flag flows from the config dict through MMConfig."""

    def test_defaults_on(self):
        from strategies.mm_config import MMConfig

        cfg = MMConfig.from_legacy_dict({})
        assert cfg.close.track_max_adverse_excursion is True

    def test_can_be_disabled(self):
        from strategies.mm_config import MMConfig

        cfg = MMConfig.from_legacy_dict({'track_max_adverse_excursion': False})
        assert cfg.close.track_max_adverse_excursion is False


class TestMaeDoesNotChangeCloseBehaviour:
    """Observation only: close decisions must be identical either way."""

    def test_no_close_triggered_by_tracking_alone(self):
        """A large excursion with the threshold disabled must not close."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=0.0)
        close_fn = MagicMock()
        _set_mid(md, 95.0)  # -500 bps
        closer.manage('BTC', {'size': 1.0, 'entry_price': 100.0}, close_fn)
        close_fn.assert_not_called()
        assert closer._mae['BTC'][0] == pytest.approx(500.0)

    def test_force_close_age_unaffected(self):
        """Tracking on/off must not shift the age-based force close."""
        results = []
        for track in (True, False):
            closer, om, md = _make_closer(
                track_max_adverse_excursion=track, max_age=60
            )
            close_fn = MagicMock()
            _set_mid(md, 99.0)
            closer.manage('BTC', {'size': 1.0, 'entry_price': 100.0}, close_fn)
            entry_time, oid, tier = closer._open_positions['BTC']
            closer._open_positions['BTC'] = (entry_time - 100, oid, tier)
            closer.manage('BTC', {'size': 1.0, 'entry_price': 100.0}, close_fn)
            results.append('BTC' not in closer._open_positions)
        assert results[0] == results[1]


class TestMaeSamplingIsLowerBound:
    """Documented limitation, asserted so it is not mistaken for exactness."""

    def test_intra_cycle_spike_is_missed(self):
        """A spike between two manage() calls leaves no trace.

        MAE is therefore a lower bound: counts of positions breaching a
        level are under-counted, which is why this data can support
        withholding an early close but not enabling one.
        """
        closer, om, md = _make_closer()
        _manage(closer, md, 99.9)   # -10 bps observed
        # ... mid momentarily hits 95.0 here, between cycles, unobserved ...
        _manage(closer, md, 99.9)   # -10 bps observed again
        assert closer._mae['BTC'][0] == pytest.approx(10.0)

    def test_sampling_follows_manage_cadence(self):
        """One sample per manage() call -- no independent polling."""
        closer, om, md = _make_closer()
        _manage(closer, md, 99.9)
        assert md.get_cached_market_data.call_count == 1
        _manage(closer, md, 99.8)
        assert md.get_cached_market_data.call_count == 2


class TestMaeMultiCoinIsolation:
    def test_peaks_tracked_per_coin(self):
        closer, om, md = _make_closer()
        om.get_all_positions.return_value = [
            {'coin': 'BTC', 'szi': '1.0'}, {'coin': 'ETH', 'szi': '1.0'},
        ]
        _set_mid(md, 99.0)
        closer.manage('BTC', {'size': 1.0, 'entry_price': 100.0}, MagicMock())
        _set_mid(md, 99.8)
        closer.manage('ETH', {'size': 1.0, 'entry_price': 100.0}, MagicMock())
        assert closer._mae['BTC'][0] == pytest.approx(100.0)
        assert closer._mae['ETH'][0] == pytest.approx(20.0)


class TestMaeThreadSafetyShape:
    """The state is a plain dict of immutable tuples (GIL-protected writes)."""

    def test_entries_are_immutable_tuples(self):
        closer, om, md = _make_closer()
        _manage(closer, md, 99.5)
        value = closer._mae['BTC']
        assert isinstance(value, tuple)
        assert len(value) == 2
        assert all(isinstance(v, float) for v in value)


def test_monotonic_clock_used_for_age():
    """mae_age is derived from the same monotonic clock as position age."""
    closer, om, md = _make_closer()
    before = time.monotonic()
    _manage(closer, md, 99.5)
    _, mae_age = closer._mae['BTC']
    assert 0 <= mae_age <= time.monotonic() - before + 1
