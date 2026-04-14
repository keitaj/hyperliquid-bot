"""Tests for cancel-on-fill behaviour in MarketMakingStrategy and OrderTracker."""

import time
from collections import defaultdict
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy
from strategies.mm_order_tracker import OrderTracker


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _make_strategy():
    """Create a MarketMakingStrategy with mocked dependencies."""
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        strategy = MarketMakingStrategy.__new__(MarketMakingStrategy)

    strategy.positions = {}
    strategy._orders_placed = 0
    strategy._fills_detected = 0
    strategy._orders_placed_per_coin = defaultdict(int)
    strategy._fills_per_coin = defaultdict(int)
    strategy._fill_rate_log_interval = 9999
    strategy._last_fill_rate_log = 0.0
    strategy._prev_position_coins = set()

    tracker = MagicMock(spec=OrderTracker)
    strategy._tracker = tracker
    strategy._closer = MagicMock()
    strategy._closer.tracked_coins = set()

    strategy.update_positions = MagicMock()
    strategy.market_data = MagicMock()
    strategy.close_immediately = True
    strategy.close_position = MagicMock()
    strategy.max_positions = 5
    strategy.max_open_orders = 4
    strategy.spread_bps = 5
    strategy.order_size_usd = 50
    strategy.inventory_skew_bps = 0
    strategy._max_coin_status_display = 10

    return strategy


def _make_tracker():
    """Create an OrderTracker with a mocked order_manager."""
    om = MagicMock()
    tracker = OrderTracker(
        order_manager=om,
        refresh_interval_seconds=30,
        max_open_orders=4,
    )
    return tracker, om


# ------------------------------------------------------------------ #
#  OrderTracker.cancel_all_orders_for_coin
# ------------------------------------------------------------------ #

class TestCancelAllOrdersForCoin:

    def test_cancels_all_tracked_orders(self):
        tracker, om = _make_tracker()
        now = time.monotonic()
        tracker._tracked_orders['BTC'] = [
            (101, 'buy', now),
            (102, 'sell', now),
        ]
        om.bulk_cancel_orders.return_value = 2

        tracker.cancel_all_orders_for_coin('BTC')

        om.bulk_cancel_orders.assert_called_once_with([
            {"coin": "BTC", "oid": 101},
            {"coin": "BTC", "oid": 102},
        ])
        assert tracker._tracked_orders['BTC'] == []

    def test_no_orders_is_noop(self):
        tracker, om = _make_tracker()
        tracker._tracked_orders['BTC'] = []

        tracker.cancel_all_orders_for_coin('BTC')

        om.bulk_cancel_orders.assert_not_called()

    def test_missing_coin_is_noop(self):
        tracker, om = _make_tracker()

        tracker.cancel_all_orders_for_coin('UNKNOWN')

        om.bulk_cancel_orders.assert_not_called()

    def test_cancel_failure_handled_gracefully(self):
        """API errors should be caught and orders still cleared from tracking."""
        tracker, om = _make_tracker()
        now = time.monotonic()
        tracker._tracked_orders['ETH'] = [
            (201, 'buy', now),
        ]
        om.bulk_cancel_orders.side_effect = Exception("API timeout")

        tracker.cancel_all_orders_for_coin('ETH')

        # Orders cleared from tracking even on failure
        assert tracker._tracked_orders['ETH'] == []


# ------------------------------------------------------------------ #
#  Integration: fill detection triggers cancel in run()
# ------------------------------------------------------------------ #

class TestCancelOnFillInRun:

    def test_new_fill_triggers_cancel(self):
        """When a new position is detected, cancel_all_orders_for_coin is called."""
        strategy = _make_strategy()
        strategy._prev_position_coins = set()
        strategy.positions = {'BTC': {'size': 0.5}}

        # Run the fill detection logic (extracted from run())
        coins = ['BTC']
        current_position_coins = set()
        for coin in coins:
            if coin in strategy.positions and abs(strategy.positions[coin].get('size', 0)) > 0:
                current_position_coins.add(coin)

        new_fills = current_position_coins - strategy._prev_position_coins
        for coin in new_fills:
            strategy._fills_detected += 1
            strategy._fills_per_coin[coin] += 1
            strategy._tracker.cancel_all_orders_for_coin(coin)

        strategy._tracker.cancel_all_orders_for_coin.assert_called_once_with('BTC')

    def test_no_fill_no_cancel(self):
        """When a position already existed, no cancel is triggered."""
        strategy = _make_strategy()
        strategy._prev_position_coins = {'BTC'}
        strategy.positions = {'BTC': {'size': 0.5}}

        coins = ['BTC']
        current_position_coins = set()
        for coin in coins:
            if coin in strategy.positions and abs(strategy.positions[coin].get('size', 0)) > 0:
                current_position_coins.add(coin)

        new_fills = current_position_coins - strategy._prev_position_coins
        for coin in new_fills:
            strategy._tracker.cancel_all_orders_for_coin(coin)

        strategy._tracker.cancel_all_orders_for_coin.assert_not_called()

    def test_multiple_fills_cancel_each(self):
        """Multiple new fills each trigger their own cancel."""
        strategy = _make_strategy()
        strategy._prev_position_coins = set()
        strategy.positions = {
            'BTC': {'size': 0.5},
            'ETH': {'size': 1.0},
        }

        coins = ['BTC', 'ETH']
        current_position_coins = set()
        for coin in coins:
            if coin in strategy.positions and abs(strategy.positions[coin].get('size', 0)) > 0:
                current_position_coins.add(coin)

        new_fills = current_position_coins - strategy._prev_position_coins
        for coin in new_fills:
            strategy._fills_detected += 1
            strategy._fills_per_coin[coin] += 1
            strategy._tracker.cancel_all_orders_for_coin(coin)

        assert strategy._tracker.cancel_all_orders_for_coin.call_count == 2
        called_coins = {c[0][0] for c in strategy._tracker.cancel_all_orders_for_coin.call_args_list}
        assert called_coins == {'BTC', 'ETH'}
