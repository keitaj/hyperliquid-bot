"""Tests for per-coin order size overrides (--coin-size-overrides)."""

import os
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(coin_size_overrides='', order_size_usd=200, **extra):
    """Create a MarketMakingStrategy with mocked dependencies."""
    market_data = MagicMock()
    order_manager = MagicMock()
    config = {
        'spread_bps': 10,
        'order_size_usd': order_size_usd,
        'max_open_orders': 4,
        'close_immediately': False,
        'max_positions': 8,
        'maker_only': True,
        'bbo_mode': True,
        'bbo_offset_bps': 1.0,
        'coin_size_overrides': coin_size_overrides,
        **extra,
    }
    strategy = MarketMakingStrategy(market_data, order_manager, config)
    return strategy


class TestGetCoinSize:
    """Tests for _get_coin_size() lookup logic."""

    def test_override_for_coin(self):
        """Override present -> use per-coin size."""
        s = _make_strategy(coin_size_overrides='TSLA:150')
        assert s._get_coin_size('xyz:TSLA') == 150.0

    def test_no_override_falls_back_to_global(self):
        """No override for coin -> use global order_size_usd."""
        s = _make_strategy(coin_size_overrides='TSLA:150', order_size_usd=200)
        assert s._get_coin_size('xyz:SP500') == 200.0

    def test_bare_name_match(self):
        """Bare name in overrides matches DEX-prefixed coin."""
        s = _make_strategy(coin_size_overrides='TSLA:150')
        assert s._get_coin_size('xyz:TSLA') == 150.0

    def test_dex_prefixed_match(self):
        """DEX-prefixed name in overrides matches exactly."""
        s = _make_strategy(coin_size_overrides='xyz:TSLA:150')
        assert s._get_coin_size('xyz:TSLA') == 150.0

    def test_dex_prefixed_does_not_match_other_dex(self):
        """DEX-prefixed override for xyz does not match km."""
        s = _make_strategy(coin_size_overrides='xyz:TSLA:150', order_size_usd=200)
        assert s._get_coin_size('km:TSLA') == 200.0

    def test_empty_overrides_all_fallback(self):
        """Empty overrides -> all coins use global."""
        s = _make_strategy(coin_size_overrides='', order_size_usd=200)
        assert s._get_coin_size('xyz:TSLA') == 200.0
        assert s._get_coin_size('xyz:SP500') == 200.0

    def test_zero_value(self):
        """Override with 0 -> size is 0 (order skipped)."""
        s = _make_strategy(coin_size_overrides='TSLA:0')
        assert s._get_coin_size('xyz:TSLA') == 0.0


class TestCalculatePositionSizeWithOverrides:
    """Tests that calculate_position_size() uses per-coin size."""

    def _setup_strategy(self, coin_size_overrides='', order_size_usd=200):
        s = _make_strategy(coin_size_overrides=coin_size_overrides, order_size_usd=order_size_usd)
        md = MagicMock()
        md.mid_price = 100.0
        s.market_data.get_market_data.return_value = md
        # _apply_account_cap returns base_size / mid_price by default in real code;
        # for testing, mock it to pass through the USD value converted to coin units
        s._apply_account_cap = MagicMock(side_effect=lambda usd, price, cap_pct: usd / price)
        return s

    def test_override_coin_uses_per_coin_size(self):
        """Coin with override -> calculate_position_size uses that size."""
        s = self._setup_strategy(coin_size_overrides='TSLA:150', order_size_usd=200)
        size = s.calculate_position_size('xyz:TSLA', {})
        # 150 / 100.0 = 1.5
        assert size == 1.5

    def test_no_override_uses_global(self):
        """Coin without override -> calculate_position_size uses global."""
        s = self._setup_strategy(coin_size_overrides='TSLA:150', order_size_usd=200)
        size = s.calculate_position_size('xyz:SP500', {})
        # 200 / 100.0 = 2.0
        assert size == 2.0

    def test_zero_override_returns_zero(self):
        """Coin with size=0 -> calculate_position_size returns 0 (order skipped)."""
        s = self._setup_strategy(coin_size_overrides='TSLA:0', order_size_usd=200)
        size = s.calculate_position_size('xyz:TSLA', {})
        assert size == 0.0

    @patch.dict(os.environ, {"RISK_LEVEL": "yellow"})
    def test_risk_level_multiplier_applied(self):
        """Per-coin size is multiplied by risk_level multiplier."""
        s = self._setup_strategy(coin_size_overrides='TSLA:150', order_size_usd=200)
        size = s.calculate_position_size('xyz:TSLA', {})
        # 150 * 0.5 (yellow) / 100.0 = 0.75
        assert size == 0.75

    @patch.dict(os.environ, {"RISK_LEVEL": "red"})
    def test_risk_level_red_returns_zero(self):
        """Red risk level -> multiplier is 0 -> size is 0."""
        s = self._setup_strategy(coin_size_overrides='TSLA:150', order_size_usd=200)
        size = s.calculate_position_size('xyz:TSLA', {})
        assert size == 0.0

    def test_empty_overrides_all_global(self):
        """Empty overrides -> all coins use global size."""
        s = self._setup_strategy(coin_size_overrides='', order_size_usd=200)
        size_tsla = s.calculate_position_size('xyz:TSLA', {})
        size_sp500 = s.calculate_position_size('xyz:SP500', {})
        assert size_tsla == size_sp500 == 2.0
