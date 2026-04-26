"""Tests for drain mode in market-making strategy.

Drain mode is triggered by the existence of a flag file. When active,
the strategy stops placing new entry orders and only manages existing
positions via the normal maker-first close flow.
"""

import os
import tempfile
from unittest.mock import MagicMock

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(drain_flag_file: str = '', **extra) -> MarketMakingStrategy:
    """Create a MarketMakingStrategy with mocked dependencies."""
    market_data = MagicMock()
    order_manager = MagicMock()
    config = {
        'spread_bps': 10,
        'order_size_usd': 200,
        'max_open_orders': 4,
        'close_immediately': False,
        'max_positions': 8,
        'maker_only': True,
        'bbo_mode': True,
        'bbo_offset_bps': 1.0,
        'drain_flag_file': drain_flag_file,
        **extra,
    }
    return MarketMakingStrategy(market_data, order_manager, config)


class TestIsDrainMode:
    """Tests for _is_drain_mode()."""

    def test_no_flag_file_configured(self):
        strategy = _make_strategy(drain_flag_file='')
        assert strategy._is_drain_mode() is False

    def test_flag_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = os.path.join(tmp, '.drain_mode')
            strategy = _make_strategy(drain_flag_file=flag)
            assert strategy._is_drain_mode() is False

    def test_flag_file_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = os.path.join(tmp, '.drain_mode')
            open(flag, 'a').close()
            strategy = _make_strategy(drain_flag_file=flag)
            assert strategy._is_drain_mode() is True

    def test_none_value_disables(self):
        strategy = _make_strategy(drain_flag_file=None)
        assert strategy._drain_flag_file == ''
        assert strategy._is_drain_mode() is False


class TestDrainModeBehavior:
    """Tests that drain mode skips new orders and still manages positions."""

    def test_drain_cancels_all_orders_on_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = os.path.join(tmp, '.drain_mode')
            open(flag, 'a').close()

            strategy = _make_strategy(drain_flag_file=flag)
            strategy.update_positions = MagicMock()
            strategy.positions = {}
            strategy._log_fill_rate = MagicMock()
            strategy._log_dynamic_age = MagicMock()
            strategy._tracker = MagicMock()
            strategy._closer = MagicMock()
            strategy._closer.tracked_coins = set()

            coins = ['xyz:SP500', 'xyz:NVDA']
            strategy.run(coins)

            assert strategy._tracker.cancel_all_orders_for_coin.call_count == 2
            strategy._tracker.cancel_all_orders_for_coin.assert_any_call('xyz:SP500')
            strategy._tracker.cancel_all_orders_for_coin.assert_any_call('xyz:NVDA')
            assert strategy._was_drain is True

    def test_drain_no_repeat_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = os.path.join(tmp, '.drain_mode')
            open(flag, 'a').close()

            strategy = _make_strategy(drain_flag_file=flag)
            strategy.update_positions = MagicMock()
            strategy.positions = {}
            strategy._log_fill_rate = MagicMock()
            strategy._log_dynamic_age = MagicMock()
            strategy._tracker = MagicMock()
            strategy._closer = MagicMock()
            strategy._closer.tracked_coins = set()

            coins = ['xyz:SP500']
            strategy.run(coins)
            strategy._tracker.cancel_all_orders_for_coin.reset_mock()

            strategy.run(coins)
            strategy._tracker.cancel_all_orders_for_coin.assert_not_called()

    def test_drain_skips_new_entry_orders(self):
        """Verify drain mode does not call bulk_place_orders."""
        with tempfile.TemporaryDirectory() as tmp:
            flag = os.path.join(tmp, '.drain_mode')
            open(flag, 'a').close()

            strategy = _make_strategy(drain_flag_file=flag)
            strategy.update_positions = MagicMock()
            strategy.positions = {}
            strategy._log_fill_rate = MagicMock()
            strategy._log_dynamic_age = MagicMock()
            strategy._tracker = MagicMock()
            strategy._tracker.get_order_count.return_value = 0
            strategy._tracker.active_coins.return_value = 0
            strategy._closer = MagicMock()
            strategy._closer.tracked_coins = set()
            strategy._closer.get_close_oid.return_value = None

            md = MagicMock()
            md.mid_price = 100.0
            md.bid = 99.9
            md.ask = 100.1
            md.book_imbalance = 0.0
            strategy.market_data.get_market_data.return_value = md
            strategy.market_data.price_rounding_params.return_value = (4, 0.01)
            strategy.market_data.round_size.return_value = 0.1

            coins = ['xyz:SP500']
            strategy.run(coins)

            strategy.order_manager.bulk_place_orders.assert_not_called()

    def test_drain_still_manages_existing_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = os.path.join(tmp, '.drain_mode')
            open(flag, 'a').close()

            strategy = _make_strategy(drain_flag_file=flag)
            strategy.update_positions = MagicMock()
            strategy.positions = {
                'xyz:SP500': {'size': 0.5, 'entryPx': '5200', 'entry_price': 5200.0}
            }
            strategy._log_fill_rate = MagicMock()
            strategy._log_dynamic_age = MagicMock()
            strategy._tracker = MagicMock()
            strategy._closer = MagicMock()
            strategy._closer.tracked_coins = set()

            coins = ['xyz:SP500']
            strategy.run(coins)

            strategy._closer.manage.assert_called_once()

    def test_exit_drain_resets_flag(self):
        """When the flag file disappears, _was_drain resets and quotes resume."""
        with tempfile.TemporaryDirectory() as tmp:
            flag = os.path.join(tmp, '.drain_mode')
            open(flag, 'a').close()

            strategy = _make_strategy(drain_flag_file=flag)
            strategy.update_positions = MagicMock()
            strategy.positions = {}
            strategy._log_fill_rate = MagicMock()
            strategy._log_dynamic_age = MagicMock()
            strategy._tracker = MagicMock()
            strategy._tracker.get_order_count.return_value = 0
            strategy._tracker.active_coins.return_value = 0
            strategy._closer = MagicMock()
            strategy._closer.tracked_coins = set()
            strategy._closer.get_close_oid.return_value = None

            md = MagicMock()
            md.mid_price = 100.0
            md.bid = 99.9
            md.ask = 100.1
            md.book_imbalance = 0.0
            strategy.market_data.get_market_data.return_value = md
            strategy.market_data.price_rounding_params.return_value = (4, 0.01)
            strategy.market_data.round_size.return_value = 0.1
            strategy.order_manager.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]

            coins = ['xyz:SP500']
            strategy.run(coins)
            assert strategy._was_drain is True

            os.remove(flag)
            strategy.run(coins)
            assert strategy._was_drain is False


class TestDrainTakesPrecedenceOverQuietHours:
    """When both drain mode and quiet hours are active, drain wins (cleaner log)."""

    def test_drain_branch_runs_when_both_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = os.path.join(tmp, '.drain_mode')
            open(flag, 'a').close()

            from datetime import datetime, timezone
            from unittest.mock import patch

            strategy = _make_strategy(drain_flag_file=flag, quiet_hours_utc='17')
            strategy.update_positions = MagicMock()
            strategy.positions = {
                'xyz:SP500': {'size': 0.5, 'entryPx': '5200', 'entry_price': 5200.0}
            }
            strategy._log_fill_rate = MagicMock()
            strategy._log_dynamic_age = MagicMock()
            strategy._tracker = MagicMock()
            strategy._closer = MagicMock()
            strategy._closer.tracked_coins = set()

            with patch('strategies.market_making_strategy.datetime') as mock_dt:
                mock_dt.now.return_value = datetime(2026, 4, 20, 17, 0, 0, tzinfo=timezone.utc)
                mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

                coins = ['xyz:SP500']
                strategy.run(coins)

            assert strategy._was_drain is True
            assert strategy._was_quiet is False


class TestDrainModeDisabled:
    """Tests that drain mode has no effect when not configured."""

    def test_no_flag_file_normal_operation(self):
        strategy = _make_strategy(drain_flag_file='')
        strategy.update_positions = MagicMock()
        strategy.positions = {}
        strategy._log_fill_rate = MagicMock()
        strategy._log_dynamic_age = MagicMock()
        strategy._tracker = MagicMock()
        strategy._tracker.get_order_count.return_value = 0
        strategy._tracker.active_coins.return_value = 0
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()
        strategy._closer.get_close_oid.return_value = None

        md = MagicMock()
        md.mid_price = 100.0
        md.bid = 99.9
        md.ask = 100.1
        md.book_imbalance = 0.0
        strategy.market_data.get_market_data.return_value = md
        strategy.market_data.price_rounding_params.return_value = (4, 0.01)
        strategy.market_data.round_size.return_value = 0.1
        strategy.order_manager.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]

        coins = ['xyz:SP500']
        strategy.run(coins)

        strategy.order_manager.bulk_place_orders.assert_called_once()
