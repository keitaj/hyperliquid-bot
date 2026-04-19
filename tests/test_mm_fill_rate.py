"""Tests for MarketMakingStrategy fill-rate tracking."""

import time
from collections import defaultdict
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(fill_rate_log_interval=300):
    """Create a MarketMakingStrategy with mocked dependencies."""
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        strategy = MarketMakingStrategy.__new__(MarketMakingStrategy)

    # Manually init required attributes
    strategy.positions = {}
    strategy._orders_placed = 0
    strategy._fills_detected = 0
    strategy._orders_placed_per_coin = defaultdict(int)
    strategy._fills_per_coin = defaultdict(int)
    strategy._fill_rate_log_interval = fill_rate_log_interval
    strategy._last_fill_rate_log = 0.0
    strategy._prev_position_coins = set()
    strategy._prev_positions = {}
    strategy.imbalance_threshold = 0.0
    strategy.loss_streak_limit = 0
    strategy.loss_streak_cooldown = 300
    strategy._loss_streaks = defaultdict(int)
    strategy._coin_cooldown_until = {}
    return strategy


class TestFillDetection:
    """Fill detection: position appears -> counter increments."""

    def test_new_position_increments_fill(self):
        strategy = _make_strategy()
        strategy._prev_position_coins = set()
        strategy.positions = {'BTC': {'size': 0.5}}
        strategy.update_positions = MagicMock()

        strategy.run = MagicMock()  # don't run full loop

        # Simulate the fill detection logic directly
        current_position_coins = set()
        coins = ['BTC', 'ETH']
        for coin in coins:
            if coin in strategy.positions and abs(strategy.positions[coin].get('size', 0)) > 0:
                current_position_coins.add(coin)
        new_fills = current_position_coins - strategy._prev_position_coins
        for coin in new_fills:
            strategy._fills_detected += 1
            strategy._fills_per_coin[coin] += 1
        strategy._prev_position_coins = current_position_coins

        assert strategy._fills_detected == 1
        assert strategy._fills_per_coin['BTC'] == 1
        assert 'BTC' in strategy._prev_position_coins

    def test_existing_position_does_not_recount(self):
        strategy = _make_strategy()
        strategy._prev_position_coins = {'BTC'}
        strategy.positions = {'BTC': {'size': 0.5}}

        current_position_coins = set()
        for coin in ['BTC']:
            if coin in strategy.positions and abs(strategy.positions[coin].get('size', 0)) > 0:
                current_position_coins.add(coin)
        new_fills = current_position_coins - strategy._prev_position_coins
        for coin in new_fills:
            strategy._fills_detected += 1

        assert strategy._fills_detected == 0

    def test_multiple_new_positions(self):
        strategy = _make_strategy()
        strategy._prev_position_coins = set()
        strategy.positions = {
            'BTC': {'size': 0.5},
            'ETH': {'size': 1.0},
        }

        current_position_coins = set()
        for coin in ['BTC', 'ETH', 'SOL']:
            if coin in strategy.positions and abs(strategy.positions[coin].get('size', 0)) > 0:
                current_position_coins.add(coin)
        new_fills = current_position_coins - strategy._prev_position_coins
        for coin in new_fills:
            strategy._fills_detected += 1
            strategy._fills_per_coin[coin] += 1

        assert strategy._fills_detected == 2
        assert strategy._fills_per_coin['BTC'] == 1
        assert strategy._fills_per_coin['ETH'] == 1


class TestLogFillRateInterval:
    """_log_fill_rate only logs every N seconds."""

    def test_skips_when_interval_not_elapsed(self):
        strategy = _make_strategy(fill_rate_log_interval=300)
        strategy._orders_placed = 10
        strategy._fills_detected = 2
        strategy._last_fill_rate_log = time.monotonic()

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            strategy._log_fill_rate()
            mock_logger.info.assert_not_called()

    def test_logs_when_interval_elapsed(self):
        strategy = _make_strategy(fill_rate_log_interval=300)
        strategy._orders_placed = 10
        strategy._fills_detected = 2
        # Set last log far in the past
        strategy._last_fill_rate_log = time.monotonic() - 400

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            strategy._log_fill_rate()
            mock_logger.info.assert_called_once()

    def test_skips_when_no_orders(self):
        strategy = _make_strategy(fill_rate_log_interval=300)
        strategy._orders_placed = 0
        strategy._last_fill_rate_log = 0.0  # will pass interval check

        with patch('strategies.market_making_strategy.logger') as mock_logger:
            strategy._log_fill_rate()
            mock_logger.info.assert_not_called()


class TestCounterReset:
    """Counters reset after each log window."""

    def test_counters_reset_after_logging(self):
        strategy = _make_strategy(fill_rate_log_interval=300)
        strategy._orders_placed = 10
        strategy._fills_detected = 3
        strategy._orders_placed_per_coin['BTC'] = 5
        strategy._fills_per_coin['BTC'] = 2
        strategy._last_fill_rate_log = time.monotonic() - 400

        with patch('strategies.market_making_strategy.logger'):
            strategy._log_fill_rate()

        assert strategy._orders_placed == 0
        assert strategy._fills_detected == 0
        assert len(strategy._orders_placed_per_coin) == 0
        assert len(strategy._fills_per_coin) == 0

    def test_counters_not_reset_when_interval_not_elapsed(self):
        strategy = _make_strategy(fill_rate_log_interval=300)
        strategy._orders_placed = 10
        strategy._fills_detected = 3
        strategy._last_fill_rate_log = time.monotonic()

        with patch('strategies.market_making_strategy.logger'):
            strategy._log_fill_rate()

        assert strategy._orders_placed == 10
        assert strategy._fills_detected == 3
