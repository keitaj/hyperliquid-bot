"""Tests for periodic dynamic-age summary log.

Verifies that _log_dynamic_age:
- emits a [mm] Dynamic age: ... line when enabled and data is present
- skips when disabled
- skips when interval has not elapsed
- resets the recent dict after logging
- _get_dynamic_position_age records the latest computation per coin
"""
import time
from collections import deque
from unittest.mock import patch

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(enabled: bool = True) -> MarketMakingStrategy:
    """Build a minimal strategy bypassing __init__."""
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **kw: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)
    s._dynamic_age_enabled = enabled
    s._dynamic_age_baseline_vol = 1.0
    s._dynamic_age_min = 60.0
    s._dynamic_age_max = 300.0
    s._base_max_position_age = 120.0
    s._dynamic_age_recent = {}
    s._dynamic_age_clamp_stats = {}
    s._dynamic_age_log_interval = 300.0
    s._last_dynamic_age_log = 0.0
    s._recent_mids = {}
    s.vol_adjust_enabled = False
    s.vol_lookback = 30
    return s


class TestGetDynamicPositionAgeRecording:
    """_get_dynamic_position_age records (avg_move_bps, age) for periodic logging."""

    def test_records_when_returning_age(self):
        s = _make_strategy(enabled=True)
        s._recent_mids['xyz:NVDA'] = deque([10000 + i * 1.0 for i in range(10)])
        s._get_dynamic_position_age('xyz:NVDA')
        assert 'xyz:NVDA' in s._dynamic_age_recent
        vol, age = s._dynamic_age_recent['xyz:NVDA']
        assert vol > 0
        assert 60.0 <= age <= 300.0

    def test_no_record_when_disabled(self):
        s = _make_strategy(enabled=False)
        s._recent_mids['xyz:NVDA'] = deque([10000 + i * 1.0 for i in range(10)])
        s._get_dynamic_position_age('xyz:NVDA')
        assert s._dynamic_age_recent == {}

    def test_no_record_when_insufficient_data(self):
        s = _make_strategy(enabled=True)
        s._recent_mids['xyz:NVDA'] = deque([10000.0, 10001.0])  # < 5
        s._get_dynamic_position_age('xyz:NVDA')
        assert s._dynamic_age_recent == {}


class TestLogDynamicAge:
    """_log_dynamic_age emits a periodic summary line."""

    def test_emits_summary_when_data_and_interval_elapsed(self):
        s = _make_strategy(enabled=True)
        s._dynamic_age_recent = {'xyz:NVDA': (2.5, 60.0), 'xyz:SP500': (0.5, 240.0)}
        # Force interval-elapsed: pretend last log was long ago
        s._last_dynamic_age_log = time.monotonic() - 1000

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            s._log_dynamic_age()
            calls = [str(c) for c in mock_logger.info.call_args_list]
            dynamic_calls = [c for c in calls if '[mm] Dynamic age:' in c]
            assert len(dynamic_calls) == 1
            log_msg = dynamic_calls[0]
            # Both coins should appear with vol/age values
            assert 'xyz:NVDA' in log_msg
            assert 'vol=2.50bps' in log_msg
            assert 'age=60s' in log_msg
            assert 'xyz:SP500' in log_msg
            assert 'vol=0.50bps' in log_msg
            assert 'age=240s' in log_msg

    def test_resets_after_log(self):
        s = _make_strategy(enabled=True)
        s._dynamic_age_recent = {'xyz:NVDA': (1.0, 120.0)}
        s._last_dynamic_age_log = time.monotonic() - 1000

        s._log_dynamic_age()
        assert s._dynamic_age_recent == {}

    def test_no_log_when_disabled(self):
        s = _make_strategy(enabled=False)
        s._dynamic_age_recent = {'xyz:NVDA': (1.0, 120.0)}
        s._last_dynamic_age_log = time.monotonic() - 1000

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            s._log_dynamic_age()
            calls = [str(c) for c in mock_logger.info.call_args_list]
            assert not any('[mm] Dynamic age:' in c for c in calls)
        # When disabled, recent dict should NOT be cleared (no log was emitted)
        assert s._dynamic_age_recent == {'xyz:NVDA': (1.0, 120.0)}

    def test_no_log_when_interval_not_elapsed(self):
        s = _make_strategy(enabled=True)
        s._dynamic_age_recent = {'xyz:NVDA': (1.0, 120.0)}
        # Last log was just now
        s._last_dynamic_age_log = time.monotonic()

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            s._log_dynamic_age()
            calls = [str(c) for c in mock_logger.info.call_args_list]
            assert not any('[mm] Dynamic age:' in c for c in calls)
        # Recent dict NOT cleared (no log)
        assert s._dynamic_age_recent == {'xyz:NVDA': (1.0, 120.0)}

    def test_no_log_when_recent_empty(self):
        s = _make_strategy(enabled=True)
        s._dynamic_age_recent = {}
        s._last_dynamic_age_log = time.monotonic() - 1000

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            s._log_dynamic_age()
            calls = [str(c) for c in mock_logger.info.call_args_list]
            assert not any('[mm] Dynamic age:' in c for c in calls)

    def test_coins_sorted_in_output(self):
        s = _make_strategy(enabled=True)
        s._dynamic_age_recent = {
            'xyz:ZEBRA': (1.0, 120.0),
            'xyz:ALPHA': (2.0, 60.0),
            'xyz:MIDDLE': (1.5, 90.0),
        }
        s._last_dynamic_age_log = time.monotonic() - 1000

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            s._log_dynamic_age()
            calls = [str(c) for c in mock_logger.info.call_args_list]
            dynamic_calls = [c for c in calls if '[mm] Dynamic age:' in c]
            log_msg = dynamic_calls[0]
            # Sorted alphabetically
            alpha_idx = log_msg.find('xyz:ALPHA')
            middle_idx = log_msg.find('xyz:MIDDLE')
            zebra_idx = log_msg.find('xyz:ZEBRA')
            assert alpha_idx < middle_idx < zebra_idx


class TestDynamicAgeClampStats:
    """Per-coin clamp counters drive the [mm] dyn-age summary lines."""

    def test_min_clamp_is_recorded_when_raw_below_min(self):
        s = _make_strategy(enabled=True)
        # raw_age = base * baseline / max(vol, baseline*0.1)
        # base=120, baseline=1.0, vol=10 → ratio=0.1, raw_age=12 < min=60 → clamp
        s._recent_mids['xyz:NVDA'] = deque([100.0 * (1 + 10 / 10000) ** i for i in range(10)])
        s._get_dynamic_position_age('xyz:NVDA')
        stats = s._dynamic_age_clamp_stats['xyz:NVDA']
        assert stats['samples'] == 1
        assert stats['min_clamp'] == 1
        assert stats['max_clamp'] == 0
        assert stats['mid'] == 0
        assert stats['raw_max'] < s._dynamic_age_min  # raw was below clamp floor

    def test_max_clamp_is_recorded_when_raw_above_max(self):
        s = _make_strategy(enabled=True)
        # vol very low (0.01) → raw_age = base * baseline / (baseline*0.1)
        # = 120 * 1.0 / 0.1 = 1200 > max=300 → clamp
        s._recent_mids['xyz:SP500'] = deque([100.0 * (1 + 0.01 / 10000) ** i for i in range(10)])
        s._get_dynamic_position_age('xyz:SP500')
        stats = s._dynamic_age_clamp_stats['xyz:SP500']
        assert stats['samples'] == 1
        assert stats['max_clamp'] == 1
        assert stats['min_clamp'] == 0
        assert stats['mid'] == 0

    def test_mid_is_recorded_when_in_range(self):
        s = _make_strategy(enabled=True)
        # vol = 1.0 (=baseline) → raw_age = base = 120, in [60, 300]
        s._recent_mids['xyz:NVDA'] = deque([100.0 * (1 + 1.0 / 10000) ** i for i in range(10)])
        s._get_dynamic_position_age('xyz:NVDA')
        stats = s._dynamic_age_clamp_stats['xyz:NVDA']
        assert stats['samples'] == 1
        assert stats['mid'] == 1
        assert stats['min_clamp'] == 0
        assert stats['max_clamp'] == 0

    def test_summary_line_emits_per_coin_clamp_pct(self):
        s = _make_strategy(enabled=True)
        # Simulate 3 min-clamp + 1 mid for one coin, all max-clamp for another.
        s._dynamic_age_clamp_stats = {
            'xyz:TSLA': {
                'min_clamp': 3, 'max_clamp': 0, 'mid': 1,
                'raw_sum': 3 * 30 + 100,  # 30s, 30s, 30s, 100s
                'raw_min': 30.0, 'raw_max': 100.0, 'samples': 4,
            },
            'xyz:SP500': {
                'min_clamp': 0, 'max_clamp': 5, 'mid': 0,
                'raw_sum': 5 * 800,
                'raw_min': 800.0, 'raw_max': 800.0, 'samples': 5,
            },
        }
        s._dynamic_age_recent = {}  # only the new clamp lines, no snapshot line
        s._last_dynamic_age_log = time.monotonic() - 1000

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            s._log_dynamic_age()
            calls = [str(c) for c in mock_logger.info.call_args_list]
            tsla_lines = [c for c in calls if '[mm] dyn-age xyz:TSLA' in c]
            sp_lines = [c for c in calls if '[mm] dyn-age xyz:SP500' in c]
            assert len(tsla_lines) == 1
            assert len(sp_lines) == 1
            assert 'samples=4' in tsla_lines[0]
            assert 'min=75%' in tsla_lines[0]
            assert 'mid=25%' in tsla_lines[0]
            assert 'max=0%' in tsla_lines[0]
            assert 'samples=5' in sp_lines[0]
            assert 'max=100%' in sp_lines[0]

    def test_clamp_stats_cleared_after_log(self):
        s = _make_strategy(enabled=True)
        s._dynamic_age_clamp_stats = {
            'xyz:NVDA': {
                'min_clamp': 1, 'max_clamp': 0, 'mid': 0,
                'raw_sum': 30.0, 'raw_min': 30.0, 'raw_max': 30.0, 'samples': 1,
            }
        }
        s._dynamic_age_recent = {'xyz:NVDA': (5.0, 60.0)}
        s._last_dynamic_age_log = time.monotonic() - 1000

        s._log_dynamic_age()
        assert s._dynamic_age_clamp_stats == {}

    def test_summary_line_sorted_by_min_clamp_pct_desc(self):
        s = _make_strategy(enabled=True)
        s._dynamic_age_clamp_stats = {
            'xyz:LOW': {
                'min_clamp': 1, 'max_clamp': 0, 'mid': 9,
                'raw_sum': 1000.0, 'raw_min': 80.0, 'raw_max': 200.0, 'samples': 10,
            },
            'xyz:HIGH': {
                'min_clamp': 9, 'max_clamp': 0, 'mid': 1,
                'raw_sum': 350.0, 'raw_min': 25.0, 'raw_max': 100.0, 'samples': 10,
            },
            'xyz:MID': {
                'min_clamp': 5, 'max_clamp': 0, 'mid': 5,
                'raw_sum': 700.0, 'raw_min': 40.0, 'raw_max': 150.0, 'samples': 10,
            },
        }
        s._dynamic_age_recent = {}
        s._last_dynamic_age_log = time.monotonic() - 1000

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            s._log_dynamic_age()
            calls = [str(c) for c in mock_logger.info.call_args_list]
            dyn_calls = [c for c in calls if '[mm] dyn-age' in c]
            # HIGH min=90% should come first, then MID 50%, then LOW 10%
            assert 'xyz:HIGH' in dyn_calls[0]
            assert 'xyz:MID' in dyn_calls[1]
            assert 'xyz:LOW' in dyn_calls[2]
