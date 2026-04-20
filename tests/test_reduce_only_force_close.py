"""Tests for reduce-only collision prevention in force close path.

Verifies that _handle_force_close() and close_position() check position
existence before sending reduce_only orders, preventing "Reduce only order
would increase position" errors when the position was already closed by
a WS fill between update_positions() and the force close attempt.
"""

import time
from unittest.mock import MagicMock, patch

from strategies.mm_position_closer import PositionCloser


class TestForceClosePositionVerify:
    """Tests for Fix 4: position verification in _handle_force_close()."""

    def _make_closer(self):
        om = MagicMock()
        md = MagicMock()
        closer = PositionCloser(
            order_manager=om, market_data=md,
            spread_bps=10, max_position_age_seconds=120,
            maker_only=True, taker_fallback_age_seconds=120,
        )
        return closer, om, md

    def test_force_close_skips_when_position_already_closed(self):
        """When position is already flat, force close is skipped."""
        closer, om, md = self._make_closer()

        # Position returns empty (already closed)
        om.get_all_positions.return_value = [
            {'coin': 'xyz:SP500', 'szi': '0'}
        ]

        close_fn = MagicMock()
        entry_time = time.monotonic() - 250  # age > 240s (taker deadline)
        closer._open_positions['xyz:SP500'] = (entry_time, None, 2)

        closer._handle_force_close(
            'xyz:SP500', 0.028, 250.0, entry_time, None, close_fn
        )

        # close_position_fn should NOT be called
        close_fn.assert_not_called()
        # Tracking should be cleared
        assert 'xyz:SP500' not in closer._open_positions

    def test_force_close_skips_when_coin_not_in_positions(self):
        """When coin is not in positions response, force close is skipped."""
        closer, om, md = self._make_closer()

        om.get_all_positions.return_value = [
            {'coin': 'xyz:NVDA', 'szi': '1.0'}
        ]

        close_fn = MagicMock()
        entry_time = time.monotonic() - 250
        closer._open_positions['xyz:SP500'] = (entry_time, None, 2)

        closer._handle_force_close(
            'xyz:SP500', 0.028, 250.0, entry_time, None, close_fn
        )

        close_fn.assert_not_called()
        assert 'xyz:SP500' not in closer._open_positions

    def test_force_close_proceeds_when_position_exists(self):
        """When position still exists, force close proceeds normally."""
        closer, om, md = self._make_closer()

        om.get_all_positions.return_value = [
            {'coin': 'xyz:SP500', 'szi': '0.028'}
        ]

        close_fn = MagicMock()
        entry_time = time.monotonic() - 250
        closer._open_positions['xyz:SP500'] = (entry_time, None, 2)

        closer._handle_force_close(
            'xyz:SP500', 0.028, 250.0, entry_time, None, close_fn
        )

        # Taker close should proceed (age > taker_deadline)
        close_fn.assert_called_once_with('xyz:SP500')

    def test_force_close_proceeds_on_api_error(self):
        """When position check fails, proceed with force close (safe fallback)."""
        closer, om, md = self._make_closer()

        om.get_all_positions.side_effect = Exception("API timeout")

        close_fn = MagicMock()
        entry_time = time.monotonic() - 250
        closer._open_positions['xyz:SP500'] = (entry_time, None, 2)

        closer._handle_force_close(
            'xyz:SP500', 0.028, 250.0, entry_time, None, close_fn
        )

        # Should still attempt close (safe fallback)
        close_fn.assert_called_once_with('xyz:SP500')

    def test_force_close_cancels_existing_close_order_before_verify(self):
        """Existing close order is cancelled before position verification."""
        closer, om, md = self._make_closer()

        om.get_all_positions.return_value = [
            {'coin': 'xyz:SP500', 'szi': '0'}  # already closed
        ]

        close_fn = MagicMock()
        entry_time = time.monotonic() - 250
        closer._open_positions['xyz:SP500'] = (entry_time, 12345, 2)

        closer._handle_force_close(
            'xyz:SP500', 0.028, 250.0, entry_time, 12345, close_fn
        )

        # Close order should be cancelled
        om.cancel_order.assert_called_once_with(12345, 'xyz:SP500')
        # But close_fn should NOT be called (position gone)
        close_fn.assert_not_called()


class TestBaseStrategyClosePositionVerify:
    """Tests for Fix 5: fresh position check in close_position()."""

    def _make_strategy(self):
        """Create a concrete strategy instance bypassing __init__."""
        from strategies.market_making_strategy import MarketMakingStrategy

        with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
            strategy = MarketMakingStrategy.__new__(MarketMakingStrategy)

        strategy.order_manager = MagicMock()
        strategy.market_data = MagicMock()
        strategy.positions = {}
        return strategy

    def test_close_skips_when_fresh_check_shows_flat(self):
        """close_position() skips when fresh API shows position=0."""
        strategy = self._make_strategy()
        strategy.positions = {'xyz:SP500': {'size': 0.028, 'entry_price': 7000.0}}

        strategy.order_manager.get_all_positions.return_value = [
            {'coin': 'xyz:SP500', 'szi': '0'}
        ]

        strategy.close_position('xyz:SP500')

        assert 'xyz:SP500' not in strategy.positions

    def test_close_proceeds_when_fresh_check_confirms_position(self):
        """close_position() proceeds when fresh API confirms position exists."""
        strategy = self._make_strategy()
        strategy.positions = {'xyz:SP500': {'size': 0.028, 'entry_price': 7000.0}}

        strategy.order_manager.get_all_positions.return_value = [
            {'coin': 'xyz:SP500', 'szi': '0.028'}
        ]

        with patch('strategies.base_strategy.close_position_market') as mock_close:
            strategy.close_position('xyz:SP500')
            mock_close.assert_called_once()
            call_args = mock_close.call_args
            assert call_args[0][1] == 0.028

    def test_close_uses_cached_on_api_failure(self):
        """close_position() falls back to cached data if fresh check fails."""
        strategy = self._make_strategy()
        strategy.positions = {'xyz:SP500': {'size': 0.028, 'entry_price': 7000.0}}

        strategy.order_manager.get_all_positions.side_effect = Exception("network error")

        with patch('strategies.base_strategy.close_position_market') as mock_close:
            strategy.close_position('xyz:SP500')
            mock_close.assert_called_once()
            call_args = mock_close.call_args
            assert call_args[0][1] == 0.028

    def test_close_skips_when_no_cached_position(self):
        """close_position() returns early when no cached position."""
        strategy = self._make_strategy()

        with patch('strategies.base_strategy.close_position_market') as mock_close:
            strategy.close_position('xyz:SP500')
            mock_close.assert_not_called()
