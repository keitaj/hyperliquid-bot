"""Tests for quiet hours feature in market-making strategy."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(quiet_hours_utc='', quiet_hours_spread_multiplier=0.0, **extra):
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
        'quiet_hours_utc': quiet_hours_utc,
        'quiet_hours_spread_multiplier': quiet_hours_spread_multiplier,
        **extra,
    }
    strategy = MarketMakingStrategy(market_data, order_manager, config)
    return strategy


class TestIsQuietHour:
    """Tests for _is_quiet_hour()."""

    def test_no_quiet_hours_returns_false(self):
        strategy = _make_strategy(quiet_hours_utc='')
        assert strategy._is_quiet_hour() is False

    @patch('strategies.market_making_strategy.datetime')
    def test_quiet_hour_match(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 17, 30, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        strategy = _make_strategy(quiet_hours_utc='17')
        assert strategy._is_quiet_hour() is True

    @patch('strategies.market_making_strategy.datetime')
    def test_quiet_hour_no_match(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 16, 59, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        strategy = _make_strategy(quiet_hours_utc='17')
        assert strategy._is_quiet_hour() is False

    @patch('strategies.market_making_strategy.datetime')
    def test_multiple_quiet_hours(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 18, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        strategy = _make_strategy(quiet_hours_utc='17,18')
        assert strategy._is_quiet_hour() is True

    def test_empty_string_no_quiet_hours(self):
        strategy = _make_strategy(quiet_hours_utc='')
        assert strategy._quiet_hours == set()

    def test_spaces_in_quiet_hours(self):
        strategy = _make_strategy(quiet_hours_utc=' 17 , 18 ')
        assert strategy._quiet_hours == {17, 18}

    def test_invalid_quiet_hour_skipped(self):
        strategy = _make_strategy(quiet_hours_utc='17,abc,18')
        assert strategy._quiet_hours == {17, 18}


class TestQuietHoursFullStop:
    """Tests for full-stop mode (multiplier=0)."""

    @patch('strategies.market_making_strategy.datetime')
    def test_quiet_hours_cancels_all_orders_on_entry(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 17, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        strategy = _make_strategy(quiet_hours_utc='17')
        strategy.update_positions = MagicMock()
        strategy.positions = {}
        strategy._log_fill_rate = MagicMock()
        strategy._tracker = MagicMock()
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()

        coins = ['xyz:SP500', 'xyz:NVDA']
        strategy.run(coins)

        # Should cancel all orders for each coin on entry
        assert strategy._tracker.cancel_all_orders_for_coin.call_count == 2
        strategy._tracker.cancel_all_orders_for_coin.assert_any_call('xyz:SP500')
        strategy._tracker.cancel_all_orders_for_coin.assert_any_call('xyz:NVDA')
        assert strategy._was_quiet is True

    @patch('strategies.market_making_strategy.datetime')
    def test_quiet_hours_no_repeat_cancel(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 17, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        strategy = _make_strategy(quiet_hours_utc='17')
        strategy.update_positions = MagicMock()
        strategy.positions = {}
        strategy._log_fill_rate = MagicMock()
        strategy._tracker = MagicMock()
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()

        coins = ['xyz:SP500']
        strategy.run(coins)
        strategy._tracker.cancel_all_orders_for_coin.reset_mock()

        # Second cycle — should NOT cancel again
        strategy.run(coins)
        strategy._tracker.cancel_all_orders_for_coin.assert_not_called()

    @patch('strategies.market_making_strategy.datetime')
    def test_quiet_hours_still_manages_positions(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 17, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        strategy = _make_strategy(quiet_hours_utc='17')
        strategy.update_positions = MagicMock()
        strategy.positions = {
            'xyz:SP500': {'size': 0.5, 'entryPx': '5200', 'entry_price': 5200.0}
        }
        strategy._log_fill_rate = MagicMock()
        strategy._tracker = MagicMock()
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()

        coins = ['xyz:SP500']
        strategy.run(coins)

        # PositionCloser should still be called for open positions
        strategy._closer.manage.assert_called_once()

    @patch('strategies.market_making_strategy.datetime')
    def test_exit_quiet_hours_resets_flag(self, mock_dt):
        strategy = _make_strategy(quiet_hours_utc='17')
        strategy.update_positions = MagicMock()
        strategy.positions = {}
        strategy._log_fill_rate = MagicMock()
        strategy._tracker = MagicMock()
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()
        strategy._was_quiet = True

        # Now it's NOT quiet hour
        mock_dt.now.return_value = datetime(2026, 4, 20, 18, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        coins = ['xyz:SP500']
        strategy.run(coins)

        assert strategy._was_quiet is False


class TestQuietHoursSpreadMultiplier:
    """Tests for spread-multiplier mode."""

    @patch('strategies.market_making_strategy.datetime')
    def test_bbo_mode_spread_multiplied(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 17, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        strategy = _make_strategy(
            quiet_hours_utc='17',
            quiet_hours_spread_multiplier=2.0,
        )
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()
        strategy.calculate_position_size = MagicMock(return_value=0.1)

        md = MagicMock()
        md.mid_price = 5200.0
        md.bid = 5199.5
        md.ask = 5200.5
        md.book_imbalance = 0.0
        strategy.market_data.get_market_data.return_value = md
        strategy.market_data.price_rounding_params.return_value = (4, 0.01)
        strategy.market_data.round_size.return_value = 0.1

        strategy.order_manager.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]
        strategy._tracker = MagicMock()
        strategy._tracker.get_order_count.return_value = 0

        strategy._place_orders('xyz:SP500')

        # Verify bulk_place_orders was called (spread-multiplier mode doesn't block)
        strategy.order_manager.bulk_place_orders.assert_called_once()
        orders = strategy.order_manager.bulk_place_orders.call_args[0][0]

        # With offset=1bps * 2.0 = 2bps, buy should be lower and sell higher
        # than with normal 1bps offset
        buy_order = [o for o in orders if o.side.value == 'buy'][0]
        sell_order = [o for o in orders if o.side.value == 'sell'][0]
        # bid * (1 - 2bps/10000) = 5199.5 * 0.9998 = 5198.46 (approx)
        assert buy_order.price < md.bid
        assert sell_order.price > md.ask

    @patch('strategies.market_making_strategy.datetime')
    def test_no_multiplier_outside_quiet_hours(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 16, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        strategy = _make_strategy(
            quiet_hours_utc='17',
            quiet_hours_spread_multiplier=2.0,
        )
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()
        strategy.calculate_position_size = MagicMock(return_value=0.1)

        md = MagicMock()
        md.mid_price = 5200.0
        md.bid = 5199.5
        md.ask = 5200.5
        md.book_imbalance = 0.0
        strategy.market_data.get_market_data.return_value = md
        strategy.market_data.price_rounding_params.return_value = (4, 0.01)
        strategy.market_data.round_size.return_value = 0.1

        strategy.order_manager.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]
        strategy._tracker = MagicMock()
        strategy._tracker.get_order_count.return_value = 0

        strategy._place_orders('xyz:SP500')

        orders = strategy.order_manager.bulk_place_orders.call_args[0][0]
        buy_order = [o for o in orders if o.side.value == 'buy'][0]
        sell_order = [o for o in orders if o.side.value == 'sell'][0]
        # Normal offset=1bps: prices should be close to BBO (within 1 tick)
        assert buy_order.price <= md.bid
        assert sell_order.price >= md.ask


class TestQuietHoursDisabled:
    """Tests that quiet hours has no effect when not configured."""

    def test_no_quiet_hours_normal_operation(self):
        strategy = _make_strategy(quiet_hours_utc='')
        strategy.update_positions = MagicMock()
        strategy.positions = {}
        strategy._log_fill_rate = MagicMock()
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

        # Should place orders normally
        strategy.order_manager.bulk_place_orders.assert_called_once()
