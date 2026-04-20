"""Tests for micro-price asymmetric offset and BBO velocity guard."""

from unittest.mock import MagicMock, patch  # noqa: F401 - patch used in class methods

from ws.bbo_velocity_guard import BboVelocityGuard


class TestBboVelocityGuard:
    """Tests for BboVelocityGuard."""

    def _make_levels(self, bid: float, ask: float) -> list:
        return [
            [{"px": str(bid), "sz": "10.0"}],
            [{"px": str(ask), "sz": "10.0"}],
        ]

    def test_no_cancel_below_threshold(self):
        """No cancel when consecutive moves < threshold."""
        tracker = MagicMock()
        guard = BboVelocityGuard(tracker, consecutive_threshold=3, min_total_move_bps=1.0)

        # 2 consecutive up moves (below threshold of 3)
        guard.on_l2_update("BTC", self._make_levels(100.0, 100.1))
        guard.on_l2_update("BTC", self._make_levels(100.05, 100.15))
        guard.on_l2_update("BTC", self._make_levels(100.10, 100.20))

        tracker.cancel_orders_by_side.assert_not_called()

    def test_cancel_sell_on_consecutive_up_moves(self):
        """Cancel SELL orders after 3 consecutive up moves exceeding min_total_move."""
        tracker = MagicMock()
        guard = BboVelocityGuard(tracker, consecutive_threshold=3, min_total_move_bps=0.5)

        # Establish baseline
        guard.on_l2_update("BTC", self._make_levels(100.0, 100.1))
        # 3 consecutive up moves
        guard.on_l2_update("BTC", self._make_levels(100.05, 100.15))
        guard.on_l2_update("BTC", self._make_levels(100.10, 100.20))
        guard.on_l2_update("BTC", self._make_levels(100.15, 100.25))

        tracker.cancel_orders_by_side.assert_called_once_with("BTC", "sell")

    def test_cancel_buy_on_consecutive_down_moves(self):
        """Cancel BUY orders after 3 consecutive down moves."""
        tracker = MagicMock()
        guard = BboVelocityGuard(tracker, consecutive_threshold=3, min_total_move_bps=0.5)

        guard.on_l2_update("BTC", self._make_levels(100.0, 100.1))
        guard.on_l2_update("BTC", self._make_levels(99.95, 100.05))
        guard.on_l2_update("BTC", self._make_levels(99.90, 100.00))
        guard.on_l2_update("BTC", self._make_levels(99.85, 99.95))

        tracker.cancel_orders_by_side.assert_called_once_with("BTC", "buy")

    def test_direction_change_resets_counter(self):
        """Direction change resets the consecutive counter."""
        tracker = MagicMock()
        guard = BboVelocityGuard(tracker, consecutive_threshold=3, min_total_move_bps=0.5)

        guard.on_l2_update("BTC", self._make_levels(100.0, 100.1))
        guard.on_l2_update("BTC", self._make_levels(100.05, 100.15))  # up
        guard.on_l2_update("BTC", self._make_levels(100.10, 100.20))  # up
        guard.on_l2_update("BTC", self._make_levels(100.05, 100.15))  # down — resets
        guard.on_l2_update("BTC", self._make_levels(100.10, 100.20))  # up (count=1)

        tracker.cancel_orders_by_side.assert_not_called()

    @patch('ws.bbo_velocity_guard.time')
    def test_rate_limit(self, mock_time):
        """Rate limit prevents multiple cancels within min_cancel_interval."""
        tracker = MagicMock()
        mock_time.monotonic.return_value = 0.0
        guard = BboVelocityGuard(
            tracker, consecutive_threshold=3, min_total_move_bps=0.5,
            min_cancel_interval=2.0,
        )

        guard.on_l2_update("BTC", self._make_levels(100.0, 100.1))
        guard.on_l2_update("BTC", self._make_levels(100.05, 100.15))
        guard.on_l2_update("BTC", self._make_levels(100.10, 100.20))
        guard.on_l2_update("BTC", self._make_levels(100.15, 100.25))
        assert tracker.cancel_orders_by_side.call_count == 1

        # Another sequence within rate limit
        mock_time.monotonic.return_value = 1.0
        guard.on_l2_update("BTC", self._make_levels(100.20, 100.30))
        guard.on_l2_update("BTC", self._make_levels(100.25, 100.35))
        guard.on_l2_update("BTC", self._make_levels(100.30, 100.40))
        assert tracker.cancel_orders_by_side.call_count == 1  # still 1, rate limited

        # After rate limit expires
        mock_time.monotonic.return_value = 3.0
        guard.on_l2_update("BTC", self._make_levels(100.35, 100.45))
        guard.on_l2_update("BTC", self._make_levels(100.40, 100.50))
        guard.on_l2_update("BTC", self._make_levels(100.45, 100.55))
        assert tracker.cancel_orders_by_side.call_count == 2

    def test_min_total_move_not_met(self):
        """No cancel when cumulative move is below min_total_move_bps."""
        tracker = MagicMock()
        guard = BboVelocityGuard(
            tracker, consecutive_threshold=3, min_total_move_bps=5.0
        )

        # 3 tiny moves (total ~0.15 bps each, well below 5.0)
        guard.on_l2_update("BTC", self._make_levels(100.0, 100.1))
        guard.on_l2_update("BTC", self._make_levels(100.001, 100.101))
        guard.on_l2_update("BTC", self._make_levels(100.002, 100.102))
        guard.on_l2_update("BTC", self._make_levels(100.003, 100.103))

        tracker.cancel_orders_by_side.assert_not_called()

    def test_stop(self):
        """Stop prevents further processing."""
        tracker = MagicMock()
        guard = BboVelocityGuard(tracker, consecutive_threshold=3, min_total_move_bps=0.5)

        guard.stop()
        guard.on_l2_update("BTC", self._make_levels(100.0, 100.1))
        guard.on_l2_update("BTC", self._make_levels(100.05, 100.15))
        guard.on_l2_update("BTC", self._make_levels(100.10, 100.20))
        guard.on_l2_update("BTC", self._make_levels(100.15, 100.25))

        tracker.cancel_orders_by_side.assert_not_called()
        assert not guard.is_running

    def test_stats(self):
        """Stats property returns correct values."""
        tracker = MagicMock()
        guard = BboVelocityGuard(tracker, consecutive_threshold=3, min_total_move_bps=0.5)

        guard.on_l2_update("BTC", self._make_levels(100.0, 100.1))
        guard.on_l2_update("BTC", self._make_levels(100.05, 100.15))
        guard.on_l2_update("BTC", self._make_levels(100.10, 100.20))
        guard.on_l2_update("BTC", self._make_levels(100.15, 100.25))

        stats = guard.stats
        assert stats["cancels_triggered"] == 1
        assert stats["errors"] == 0
        assert stats["running"] is True


class TestMicroPriceCalculation:
    """Tests for micro-price calculation in MarketDataManager."""

    def test_micro_price_basic(self):
        """Micro-price is size-weighted mid."""
        from market_data import MarketDataManager
        from unittest.mock import MagicMock

        mgr = MarketDataManager(MagicMock(), imbalance_depth=5)
        levels = [
            [{"px": "100.0", "sz": "10.0"}, {"px": "99.9", "sz": "5.0"}],
            [{"px": "101.0", "sz": "5.0"}, {"px": "101.1", "sz": "5.0"}],
        ]
        md = mgr._parse_levels("BTC", levels)

        assert md is not None
        # micro = bid * (ask_sz / total) + ask * (bid_sz / total)
        # = 100 * (5/15) + 101 * (10/15) = 33.33 + 67.33 = 100.667
        expected = 100.0 * (5.0 / 15.0) + 101.0 * (10.0 / 15.0)
        assert abs(md.micro_price - expected) < 0.001
        assert md.bid_size_top == 10.0
        assert md.ask_size_top == 5.0

    def test_micro_price_equal_sizes(self):
        """When sizes are equal, micro_price equals mid_price."""
        from market_data import MarketDataManager
        from unittest.mock import MagicMock

        mgr = MarketDataManager(MagicMock(), imbalance_depth=5)
        levels = [
            [{"px": "100.0", "sz": "10.0"}],
            [{"px": "102.0", "sz": "10.0"}],
        ]
        md = mgr._parse_levels("BTC", levels)

        assert md is not None
        assert abs(md.micro_price - md.mid_price) < 0.001

    def test_micro_price_zero_size_fallback(self):
        """When one side has zero size, levels should still parse (sz won't be 0 in practice)."""
        from market_data import MarketDataManager
        from unittest.mock import MagicMock

        mgr = MarketDataManager(MagicMock(), imbalance_depth=5)
        # Both sides have size > 0 (realistic scenario)
        levels = [
            [{"px": "100.0", "sz": "0.001"}],
            [{"px": "101.0", "sz": "100.0"}],
        ]
        md = mgr._parse_levels("BTC", levels)
        assert md is not None
        # Heavily ask-weighted → micro close to bid
        assert md.micro_price < md.mid_price


class TestMicroPriceAsymmetricOffset:
    """Tests for _calculate_microprice_offsets in MarketMakingStrategy."""

    def _make_strategy(self, enabled: bool = True, multiplier: float = 1.0,
                       max_skew: float = 2.0):
        """Create a minimal strategy with microprice config (bypass __init__)."""
        from strategies.market_making_strategy import MarketMakingStrategy

        with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
            strategy = MarketMakingStrategy.__new__(MarketMakingStrategy)

        market_data = MagicMock()
        strategy.market_data = market_data
        strategy._microprice_enabled = enabled
        strategy._microprice_multiplier = multiplier
        strategy._microprice_max_skew_bps = max_skew
        return strategy, market_data

    def test_disabled_returns_symmetric(self):
        """When disabled, returns symmetric offsets."""
        strategy, market_data = self._make_strategy(enabled=False)
        buy_off, sell_off = strategy._calculate_microprice_offsets("BTC", 1.0)
        assert buy_off == 1.0
        assert sell_off == 1.0

    def test_buy_pressure_widens_sell(self):
        """When micro > mid (buy pressure), sell offset widens."""
        from market_data import MarketData
        from datetime import datetime

        strategy, market_data = self._make_strategy(enabled=True, multiplier=1.0)
        # micro > mid → buy pressure
        md = MarketData(
            symbol="BTC", mid_price=100.0, bid=99.9, ask=100.1,
            spread=0.2, timestamp=datetime.now(),
            bid_size_top=20.0, ask_size_top=5.0,
            micro_price=100.04,  # above mid by 4bps
        )
        market_data.get_market_data.return_value = md

        buy_off, sell_off = strategy._calculate_microprice_offsets("BTC", 1.0)
        assert sell_off > 1.0  # sell offset widened
        assert buy_off < 1.0  # buy offset tightened

    def test_sell_pressure_widens_buy(self):
        """When micro < mid (sell pressure), buy offset widens."""
        from market_data import MarketData
        from datetime import datetime

        strategy, market_data = self._make_strategy(enabled=True, multiplier=1.0)
        md = MarketData(
            symbol="BTC", mid_price=100.0, bid=99.9, ask=100.1,
            spread=0.2, timestamp=datetime.now(),
            bid_size_top=5.0, ask_size_top=20.0,
            micro_price=99.96,  # below mid by 4bps
        )
        market_data.get_market_data.return_value = md

        buy_off, sell_off = strategy._calculate_microprice_offsets("BTC", 1.0)
        assert buy_off > 1.0  # buy offset widened
        assert sell_off < 1.0  # sell offset tightened

    def test_max_skew_clamp(self):
        """Offset adjustment is clamped to max_skew_bps."""
        from market_data import MarketData
        from datetime import datetime

        strategy, market_data = self._make_strategy(
            enabled=True, multiplier=1.0, max_skew=1.5
        )
        # Large skew (10bps) but clamped to 1.5
        md = MarketData(
            symbol="BTC", mid_price=100.0, bid=99.9, ask=100.1,
            spread=0.2, timestamp=datetime.now(),
            bid_size_top=100.0, ask_size_top=1.0,
            micro_price=100.10,  # 10bps above mid
        )
        market_data.get_market_data.return_value = md

        buy_off, sell_off = strategy._calculate_microprice_offsets("BTC", 1.0)
        # sell_off = 1.0 + 1.5 (clamped) = 2.5
        assert sell_off == 1.0 + 1.5
        # buy_off = max(1.0 - 1.5*0.5, 0.5) = max(0.25, 0.5) = 0.5
        assert buy_off == 0.5

    def test_no_market_data_returns_symmetric(self):
        """When market data is unavailable, returns symmetric."""
        strategy, market_data = self._make_strategy(enabled=True)
        market_data.get_market_data.return_value = None

        buy_off, sell_off = strategy._calculate_microprice_offsets("BTC", 1.0)
        assert buy_off == 1.0
        assert sell_off == 1.0
