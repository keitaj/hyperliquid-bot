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
