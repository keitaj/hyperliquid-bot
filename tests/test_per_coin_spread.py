"""Tests for per-coin spread/offset overrides."""

from unittest.mock import MagicMock

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(coin_offset_overrides='', coin_spread_overrides='', **extra):
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
        'coin_offset_overrides': coin_offset_overrides,
        'coin_spread_overrides': coin_spread_overrides,
        **extra,
    }
    strategy = MarketMakingStrategy(market_data, order_manager, config)
    return strategy


class TestParseCoinOverrides:
    """Tests for _parse_coin_overrides()."""

    def test_empty_string(self):
        result = MarketMakingStrategy._parse_coin_overrides('')
        assert result == {}

    def test_single_coin(self):
        result = MarketMakingStrategy._parse_coin_overrides('SP500:1.5')
        assert result == {'SP500': 1.5}

    def test_multiple_coins(self):
        result = MarketMakingStrategy._parse_coin_overrides('SP500:0.5,MSFT:3,XYZ100:2')
        assert result == {'SP500': 0.5, 'MSFT': 3.0, 'XYZ100': 2.0}

    def test_dex_prefixed(self):
        result = MarketMakingStrategy._parse_coin_overrides('xyz:SP500:0.5')
        assert result == {'xyz:SP500': 0.5}

    def test_spaces_stripped(self):
        result = MarketMakingStrategy._parse_coin_overrides(' SP500:1.5 , MSFT:3 ')
        assert result == {'SP500': 1.5, 'MSFT': 3.0}

    def test_invalid_format_skipped(self):
        result = MarketMakingStrategy._parse_coin_overrides('SP500,MSFT:3')
        # 'SP500' has no colon → rsplit gives ['SP500'] → skipped
        # Actually rsplit(':', 1) on 'SP500' gives ['SP500'] which has len 1
        assert 'MSFT' in result
        assert result['MSFT'] == 3.0

    def test_invalid_value_skipped(self):
        result = MarketMakingStrategy._parse_coin_overrides('SP500:abc,MSFT:3')
        assert 'SP500' not in result
        assert result['MSFT'] == 3.0

    def test_empty_entries_skipped(self):
        result = MarketMakingStrategy._parse_coin_overrides('SP500:1.5,,MSFT:3,')
        assert result == {'SP500': 1.5, 'MSFT': 3.0}


class TestGetCoinOffset:
    """Tests for _get_coin_offset() lookup logic."""

    def test_exact_match(self):
        s = _make_strategy(coin_offset_overrides='xyz:SP500:0.5')
        assert s._get_coin_offset('xyz:SP500') == 0.5

    def test_bare_name_match(self):
        s = _make_strategy(coin_offset_overrides='SP500:0.5')
        assert s._get_coin_offset('xyz:SP500') == 0.5

    def test_bare_name_matches_any_dex(self):
        s = _make_strategy(coin_offset_overrides='SP500:0.5')
        assert s._get_coin_offset('km:SP500') == 0.5

    def test_dex_specific_takes_priority(self):
        s = _make_strategy(coin_offset_overrides='xyz:SP500:0.5,SP500:2.0')
        # Full match 'xyz:SP500' should be found first
        assert s._get_coin_offset('xyz:SP500') == 0.5
        # 'km:SP500' falls through to bare match
        assert s._get_coin_offset('km:SP500') == 2.0

    def test_no_override_returns_global(self):
        s = _make_strategy(coin_offset_overrides='MSFT:3')
        assert s._get_coin_offset('xyz:SP500') == 1.0  # global bbo_offset_bps

    def test_empty_overrides_returns_global(self):
        s = _make_strategy(coin_offset_overrides='')
        assert s._get_coin_offset('xyz:SP500') == 1.0


class TestGetCoinSpread:
    """Tests for _get_coin_spread() lookup logic."""

    def test_override_found(self):
        s = _make_strategy(coin_spread_overrides='SP500:15')
        assert s._get_coin_spread('xyz:SP500') == 15.0

    def test_no_override_returns_global(self):
        s = _make_strategy(coin_spread_overrides='MSFT:15')
        assert s._get_coin_spread('xyz:SP500') == 10.0  # global spread_bps


class TestPerCoinOffsetInPlaceOrders:
    """Tests that per-coin offset is applied in _place_orders."""

    def test_different_coins_get_different_offsets(self):
        s = _make_strategy(coin_offset_overrides='SP500:0.5,MSFT:3')
        s._closer = MagicMock()
        s._closer.tracked_coins = set()
        s.calculate_position_size = MagicMock(return_value=0.1)
        s._tracker = MagicMock()
        s._tracker.get_order_count.return_value = 0

        md = MagicMock()
        md.mid_price = 5200.0
        md.bid = 5199.5
        md.ask = 5200.5
        md.book_imbalance = 0.0
        s.market_data.get_market_data.return_value = md
        s.market_data.price_rounding_params.return_value = (4, 0.01)
        s.market_data.round_size.return_value = 0.1
        s.order_manager.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]

        # Place orders for SP500 (offset=0.5)
        s._place_orders('xyz:SP500')
        sp500_orders = s.order_manager.bulk_place_orders.call_args[0][0]
        sp500_buy = [o for o in sp500_orders if o.side.value == 'buy'][0]

        s.order_manager.bulk_place_orders.reset_mock()

        # Place orders for MSFT (offset=3)
        s._place_orders('xyz:MSFT')
        msft_orders = s.order_manager.bulk_place_orders.call_args[0][0]
        msft_buy = [o for o in msft_orders if o.side.value == 'buy'][0]

        # MSFT buy should be lower than SP500 buy (wider offset)
        assert msft_buy.price < sp500_buy.price

    def test_no_override_uses_global(self):
        s = _make_strategy(coin_offset_overrides='')
        s._closer = MagicMock()
        s._closer.tracked_coins = set()
        s.calculate_position_size = MagicMock(return_value=0.1)
        s._tracker = MagicMock()
        s._tracker.get_order_count.return_value = 0

        md = MagicMock()
        md.mid_price = 100.0
        md.bid = 99.9
        md.ask = 100.1
        md.book_imbalance = 0.0
        s.market_data.get_market_data.return_value = md
        s.market_data.price_rounding_params.return_value = (4, 0.01)
        s.market_data.round_size.return_value = 0.1
        s.order_manager.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]

        s._place_orders('xyz:SP500')

        # Should use global offset (1.0 bps) — orders are placed normally
        s.order_manager.bulk_place_orders.assert_called_once()


class TestPerCoinSpreadInPositionCloser:
    """Tests that per-coin spread is used by PositionCloser."""

    def test_closer_receives_overrides(self):
        s = _make_strategy(coin_spread_overrides='SP500:15,MSFT:20')
        assert s._closer._coin_spread_overrides == {'SP500': 15.0, 'MSFT': 20.0}

    def test_closer_spread_lookup(self):
        s = _make_strategy(coin_spread_overrides='SP500:15')
        assert s._closer._get_spread_for_coin('xyz:SP500') == 15.0
        assert s._closer._get_spread_for_coin('xyz:NVDA') == 10.0  # global default

    def test_empty_overrides_no_closer_change(self):
        s = _make_strategy(coin_spread_overrides='')
        assert s._closer._coin_spread_overrides == {}
        assert s._closer._get_spread_for_coin('xyz:SP500') == 10.0


class TestVolAdjustWithPerCoinOffset:
    """Tests that vol_adjust uses per-coin offset as base."""

    def test_vol_adjust_base_from_override(self):
        s = _make_strategy(
            coin_offset_overrides='SP500:3',
            vol_adjust_enabled=True,
            vol_adjust_multiplier=2.0,
            vol_lookback=30,
        )
        # With override=3, vol_adjust should use 3 as base, not global 1.0
        # Without enough mids data, should return base directly
        assert s._get_volatility_adjusted_offset('xyz:SP500', 3.0) == 3.0

    def test_vol_adjust_base_from_global_when_no_override(self):
        s = _make_strategy(vol_adjust_enabled=True)
        assert s._get_volatility_adjusted_offset('xyz:SP500') == 1.0
