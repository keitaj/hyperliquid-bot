"""Tests for hourly spread schedule feature in market-making strategy."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(spread_schedule='', **extra):
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
        'spread_schedule': spread_schedule,
        **extra,
    }
    strategy = MarketMakingStrategy(market_data, order_manager, config)
    return strategy


# ── Parsing ──────────────────────────────────────────────────────────

class TestParseSpreadSchedule:
    """Tests for _parse_spread_schedule()."""

    def test_empty_string(self):
        result = MarketMakingStrategy._parse_spread_schedule('')
        assert result == {}

    def test_none(self):
        result = MarketMakingStrategy._parse_spread_schedule(None)
        assert result == {}

    def test_single_entry(self):
        result = MarketMakingStrategy._parse_spread_schedule('14:1.5')
        assert result == {14: 1.5}

    def test_multiple_entries(self):
        result = MarketMakingStrategy._parse_spread_schedule('0:1.5,3:2.0,14:1.5,15:1.5')
        assert result == {0: 1.5, 3: 2.0, 14: 1.5, 15: 1.5}

    def test_zero_multiplier(self):
        result = MarketMakingStrategy._parse_spread_schedule('17:0')
        assert result == {17: 0.0}

    def test_boundary_hours(self):
        result = MarketMakingStrategy._parse_spread_schedule('0:1.2,23:1.8')
        assert result == {0: 1.2, 23: 1.8}

    def test_invalid_hour_24_skipped(self):
        result = MarketMakingStrategy._parse_spread_schedule('24:1.5,14:2.0')
        assert result == {14: 2.0}

    def test_negative_hour_skipped(self):
        result = MarketMakingStrategy._parse_spread_schedule('-1:1.5,14:2.0')
        assert result == {14: 2.0}

    def test_negative_multiplier_skipped(self):
        result = MarketMakingStrategy._parse_spread_schedule('14:-1.5')
        assert result == {}

    def test_invalid_format_skipped(self):
        result = MarketMakingStrategy._parse_spread_schedule('abc,14:2.0,xyz:abc')
        assert result == {14: 2.0}

    def test_spaces_trimmed(self):
        result = MarketMakingStrategy._parse_spread_schedule(' 14 : 1.5 , 15 : 2.0 ')
        assert result == {14: 1.5, 15: 2.0}

    def test_trailing_comma(self):
        result = MarketMakingStrategy._parse_spread_schedule('14:1.5,')
        assert result == {14: 1.5}


# ── Multiplier retrieval ─────────────────────────────────────────────

class TestGetHourlySpreadMultiplier:
    """Tests for _get_hourly_spread_multiplier()."""

    def test_no_schedule_returns_1(self):
        strategy = _make_strategy(spread_schedule='')
        assert strategy._get_hourly_spread_multiplier() == 1.0

    @patch('strategies.market_making_strategy.datetime')
    def test_scheduled_hour_returns_multiplier(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 14, 30, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        strategy = _make_strategy(spread_schedule='14:2.0,15:1.5')
        assert strategy._get_hourly_spread_multiplier() == 2.0

    @patch('strategies.market_making_strategy.datetime')
    def test_unscheduled_hour_returns_1(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 16, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        strategy = _make_strategy(spread_schedule='14:2.0,15:1.5')
        assert strategy._get_hourly_spread_multiplier() == 1.0


# ── Quiet hour integration ───────────────────────────────────────────

class TestSpreadScheduleQuietHours:
    """Tests for spread_schedule with multiplier 0 triggering quiet mode."""

    @patch('strategies.market_making_strategy.datetime')
    def test_schedule_zero_triggers_quiet(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 17, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        strategy = _make_strategy(spread_schedule='17:0')
        assert strategy._is_quiet_hour() is True

    @patch('strategies.market_making_strategy.datetime')
    def test_schedule_nonzero_does_not_trigger_quiet(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 14, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        strategy = _make_strategy(spread_schedule='14:1.5')
        assert strategy._is_quiet_hour() is False

    @patch('strategies.market_making_strategy.datetime')
    def test_quiet_hours_takes_priority(self, mock_dt):
        """quiet_hours_utc full-stop overrides spread_schedule."""
        mock_dt.now.return_value = datetime(2026, 4, 20, 17, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        strategy = _make_strategy(
            spread_schedule='17:1.5',
            quiet_hours_utc='17',
            quiet_hours_spread_multiplier=0.0,
        )
        # quiet_hours full-stop takes priority, so is_quiet should be True
        assert strategy._is_quiet_hour() is True

    @patch('strategies.market_making_strategy.datetime')
    def test_no_quiet_no_schedule_not_quiet(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 14, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        strategy = _make_strategy(spread_schedule='', quiet_hours_utc='')
        assert strategy._is_quiet_hour() is False


# ── BBO mode offset application ──────────────────────────────────────

class TestBboModeSpreadSchedule:
    """Tests that spread_schedule multiplier is applied in BBO mode pricing."""

    @patch('strategies.market_making_strategy.datetime')
    def test_bbo_offset_multiplied(self, mock_dt):
        """Verify BBO offset is multiplied by schedule in _place_orders."""
        mock_dt.now.return_value = datetime(2026, 4, 20, 14, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        strategy = _make_strategy(spread_schedule='14:2.0', bbo_offset_bps=1.0)

        # Mock market data
        md = MagicMock()
        md.bid = 100.0
        md.ask = 100.02
        md.mid_price = 100.01
        md.book_imbalance = 0.0
        md.micro_price = 0  # disabled
        strategy.market_data.get_market_data.return_value = md
        strategy.market_data.price_rounding_params.return_value = (2, True)
        strategy.market_data.round_size.return_value = 2.0
        strategy.positions = {}
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()
        strategy._closer.get_close_oid.return_value = None
        strategy._tracker = MagicMock()
        strategy._tracker.get_order_count.return_value = 0
        strategy._tracker.active_coins.return_value = 0

        # Mock order placement to capture prices
        placed_orders = []

        def capture_orders(orders):
            placed_orders.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]

        strategy.order_manager.bulk_place_orders = capture_orders

        strategy._place_orders('xyz:SP500')

        assert len(placed_orders) == 2
        buy_order = placed_orders[0]
        sell_order = placed_orders[1]

        # With offset=1bps, multiplier=2.0: effective_offset=2bps
        # buy_price = 100.0 * (1 - 2/10000) = 100.0 * 0.9998 = 99.98
        # sell_price = 100.02 * (1 + 2/10000) = 100.02 * 1.0002 = 100.04
        assert buy_order.price == pytest.approx(99.98, abs=0.01)
        assert sell_order.price == pytest.approx(100.04, abs=0.01)

    @patch('strategies.market_making_strategy.datetime')
    def test_no_schedule_offset_unchanged(self, mock_dt):
        """Without schedule, offset stays at base value."""
        mock_dt.now.return_value = datetime(2026, 4, 20, 14, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        strategy = _make_strategy(spread_schedule='', bbo_offset_bps=1.0)

        md = MagicMock()
        md.bid = 100.0
        md.ask = 100.02
        md.mid_price = 100.01
        md.book_imbalance = 0.0
        md.micro_price = 0
        strategy.market_data.get_market_data.return_value = md
        strategy.market_data.price_rounding_params.return_value = (2, True)
        strategy.market_data.round_size.return_value = 2.0
        strategy.positions = {}
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()
        strategy._closer.get_close_oid.return_value = None
        strategy._tracker = MagicMock()
        strategy._tracker.get_order_count.return_value = 0
        strategy._tracker.active_coins.return_value = 0

        placed_orders = []

        def capture_orders(orders):
            placed_orders.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]

        strategy.order_manager.bulk_place_orders = capture_orders

        strategy._place_orders('xyz:SP500')

        assert len(placed_orders) == 2
        buy_order = placed_orders[0]
        sell_order = placed_orders[1]

        # With offset=1bps, no multiplier: effective_offset=1bps
        # buy_price = 100.0 * (1 - 1/10000) = 99.99
        # sell_price = 100.02 * (1 + 1/10000) = 100.03
        assert buy_order.price == pytest.approx(99.99, abs=0.01)
        assert sell_order.price == pytest.approx(100.03, abs=0.01)


# ── Full-stop via spread_schedule=0 ─────────────────────────────────

class TestSpreadScheduleFullStop:
    """Tests that spread_schedule with multiplier 0 triggers order cancellation."""

    @patch('strategies.market_making_strategy.datetime')
    def test_zero_multiplier_cancels_orders(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 17, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        strategy = _make_strategy(spread_schedule='17:0')
        strategy.update_positions = MagicMock()
        strategy.positions = {}
        strategy._tracker = MagicMock()
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()

        coins = ['xyz:SP500', 'xyz:NVDA']
        strategy.run(coins)

        # Should cancel all orders for each coin (quiet mode)
        assert strategy._tracker.cancel_all_orders_for_coin.call_count == 2


# ── Cycle log suffix ────────────────────────────────────────────────

class TestCycleLogSuffix:
    """Tests that cycle log shows [SPREAD×N] suffix."""

    @patch('strategies.market_making_strategy.datetime')
    def test_log_shows_multiplier(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 4, 20, 14, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        strategy = _make_strategy(spread_schedule='14:2.0')
        strategy.update_positions = MagicMock()
        strategy.positions = {}
        strategy._tracker = MagicMock()
        strategy._tracker.get_order_count.return_value = 4  # max, skip placing
        strategy._tracker.active_coins.return_value = 0
        strategy._closer = MagicMock()
        strategy._closer.tracked_coins = set()
        strategy._closer.get_close_oid.return_value = None

        coins = ['xyz:SP500']

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            strategy.run(coins)

        # Find the cycle log call
        cycle_calls = [
            call for call in mock_logger.info.call_args_list
            if '[cycle]' in str(call)
        ]
        assert len(cycle_calls) >= 1
        log_msg = str(cycle_calls[-1])
        assert '[SPREAD' in log_msg and '2.0' in log_msg
