"""Startup handling of delisted and unpriced coins.

A single coin with no order book used to abort startup for every coin.
Delisted HIP-3 coins are now dropped while building the coin list, and with
SKIP_UNAVAILABLE_COINS a coin whose price cannot be fetched is dropped for the
session -- unless it holds a position or nothing would be left to trade.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from bot import HyperliquidBot
from hip3.dex_registry import DEXRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(**overrides):
    cfg = SimpleNamespace(
        DEX_COINS={},
        SKIP_UNAVAILABLE_COINS=False,
        STARTUP_PRICE_RETRIES=0,
        STARTUP_PRICE_RETRY_INTERVAL=0.0,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _make_bot(coins, prices, user_state=None):
    """Bot with only the attributes the startup checks touch.

    *prices* maps coin -> mid price, or None for "no order book".  A value
    may also be a list, consumed one entry per call, to simulate retries.
    """
    bot = HyperliquidBot.__new__(HyperliquidBot)
    bot.trading_coins = list(coins)
    bot.coins = bot.trading_coins
    bot.account_address = "0xtest"

    remaining = {c: list(v) if isinstance(v, list) else v for c, v in prices.items()}

    def get_market_data(coin):
        value = remaining.get(coin)
        if isinstance(value, list):
            value = value.pop(0)
        return None if value is None else SimpleNamespace(mid_price=value)

    bot.market_data = MagicMock()
    bot.market_data.get_market_data.side_effect = get_market_data

    bot.info = MagicMock()
    bot.info.user_state.return_value = user_state if user_state is not None else {"assetPositions": []}
    return bot


def _position(coin, szi):
    return {"position": {"coin": coin, "szi": str(szi)}}


# ---------------------------------------------------------------------------
# DEXRegistry
# ---------------------------------------------------------------------------

class TestRegistryIsDelisted:

    @patch("hip3.dex_registry.requests.post")
    def test_delisted_flag_is_read_from_meta(self, mock_post):
        def post(url, json=None, **kwargs):
            resp = MagicMock()
            if json["type"] == "perpDexs":
                resp.json.return_value = [None, {"name": "mkts"}]
            else:
                resp.json.return_value = {"universe": [
                    {"name": "mkts:USTECH", "szDecimals": 3, "isDelisted": True},
                    {"name": "mkts:GOLD", "szDecimals": 3},
                ]}
            return resp
        mock_post.side_effect = post

        registry = DEXRegistry("https://api.example.com")
        registry.discover(["mkts"])

        assert registry.is_delisted("mkts", "USTECH") is True
        # Live assets carry no isDelisted key at all
        assert registry.is_delisted("mkts", "GOLD") is False

    def test_unknown_dex_or_coin_is_not_delisted(self):
        registry = DEXRegistry("https://api.example.com")
        assert registry.is_delisted("nope", "X") is False


# ---------------------------------------------------------------------------
# Coin list construction
# ---------------------------------------------------------------------------

class TestBuildHip3Coins:

    def _bot(self, dexes, delisted, discovered=None):
        bot = HyperliquidBot.__new__(HyperliquidBot)
        bot.hip3_dexes = dexes
        bot.registry = MagicMock()
        bot.registry.is_delisted.side_effect = lambda dex, coin: f"{dex}:{coin}" in delisted
        bot.registry.list_coins.side_effect = lambda dex: (discovered or {}).get(dex, [])
        return bot

    def test_explicit_delisted_coin_is_dropped_with_warning(self, caplog):
        bot = self._bot(["xyz", "mkts"], delisted={"mkts:USTECH"})
        cfg = _config(DEX_COINS={"xyz": ["SP500", "NVDA"], "mkts": ["USTECH"]})

        with patch("bot.Config", cfg), caplog.at_level(logging.WARNING, logger="bot"):
            coins, delisted = bot._build_hip3_coins()

        assert coins == ["xyz:SP500", "xyz:NVDA"]
        assert delisted == ["mkts:USTECH"]
        assert any("[coin-skip] mkts:USTECH reason=delisted" in r.message for r in caplog.records)

    def test_discovered_delisted_coins_are_dropped_with_one_info_line(self, caplog):
        bot = self._bot(["xyz"], delisted={"xyz:VIX", "xyz:DXY"},
                        discovered={"xyz": ["SP500", "VIX", "DXY"]})

        with patch("bot.Config", _config()), caplog.at_level(logging.INFO, logger="bot"):
            coins, delisted = bot._build_hip3_coins()

        assert coins == ["xyz:SP500"]
        assert delisted == ["xyz:VIX", "xyz:DXY"]
        # Nobody asked for discovered coins, so no per-coin [coin-skip] warnings
        assert not any("[coin-skip]" in r.message for r in caplog.records)
        assert any("Skipped 2 delisted coins discovered on 'xyz'" in r.message for r in caplog.records)

    def test_no_delisted_coins_keeps_list_unchanged(self):
        bot = self._bot(["xyz"], delisted=set())
        cfg = _config(DEX_COINS={"xyz": ["SP500", "NVDA"]})

        with patch("bot.Config", cfg):
            coins, delisted = bot._build_hip3_coins()

        assert coins == ["xyz:SP500", "xyz:NVDA"]
        assert delisted == []


# ---------------------------------------------------------------------------
# Startup price resolution
# ---------------------------------------------------------------------------

class TestResolveStartupPrices:

    def test_all_prices_available_returns_no_exclusions(self):
        bot = _make_bot(["xyz:A", "xyz:B"], {"xyz:A": 1.0, "xyz:B": 2.0})
        with patch("bot.Config", _config(SKIP_UNAVAILABLE_COINS=True)):
            assert bot._resolve_startup_prices() == ({"xyz:A": 1.0, "xyz:B": 2.0}, [])

    def test_missing_price_aborts_when_flag_off(self, caplog):
        """Default behaviour is unchanged: one missing price aborts startup."""
        bot = _make_bot(["xyz:A", "mkts:B"], {"xyz:A": 1.0, "mkts:B": None})
        with patch("bot.Config", _config()), caplog.at_level(logging.ERROR, logger="bot"):
            assert bot._resolve_startup_prices() is None
        assert any("Could not get market price for mkts:B" in r.message for r in caplog.records)
        bot.info.user_state.assert_not_called()

    def test_zero_retries_fetches_each_coin_once(self):
        bot = _make_bot(["xyz:A", "mkts:B"], {"xyz:A": 1.0, "mkts:B": None})
        with patch("bot.Config", _config(STARTUP_PRICE_RETRIES=0)):
            bot._resolve_startup_prices()
        assert bot.market_data.get_market_data.call_count == 2

    def test_missing_price_is_skipped_when_flag_on(self, caplog):
        bot = _make_bot(["xyz:A", "mkts:B"], {"xyz:A": 1.0, "mkts:B": None})
        cfg = _config(SKIP_UNAVAILABLE_COINS=True, STARTUP_PRICE_RETRIES=2, STARTUP_PRICE_RETRY_INTERVAL=0.25)
        with patch("bot.Config", cfg), patch("time.sleep") as sleep, \
                caplog.at_level(logging.ERROR, logger="bot"):
            result = bot._resolve_startup_prices()

        assert result == ({"xyz:A": 1.0}, ["mkts:B"])
        # 1 initial attempt + 2 retries for the unpriced coin, 1 for the priced one
        assert bot.market_data.get_market_data.call_count == 4
        # The shared rate limiter may also sleep depending on earlier tests, so
        # count only the retry-interval sleeps.
        assert sleep.call_args_list.count(call(0.25)) == 2
        bot.info.user_state.assert_called_once_with("0xtest", dex="mkts")
        assert any("[coin-skip] mkts:B reason=no_price retries=2" in r.message for r in caplog.records)

    def test_price_recovered_on_retry_is_not_skipped(self):
        bot = _make_bot(["xyz:A", "xyz:B"], {"xyz:A": 1.0, "xyz:B": [None, 5.0]})
        cfg = _config(SKIP_UNAVAILABLE_COINS=True, STARTUP_PRICE_RETRIES=3)
        with patch("bot.Config", cfg), patch("time.sleep"):
            assert bot._resolve_startup_prices() == ({"xyz:A": 1.0, "xyz:B": 5.0}, [])

    def test_open_position_prevents_skip(self, caplog):
        state = {"assetPositions": [_position("B", "0.5")]}
        bot = _make_bot(["xyz:A", "mkts:B"], {"xyz:A": 1.0, "mkts:B": None}, user_state=state)
        with patch("bot.Config", _config(SKIP_UNAVAILABLE_COINS=True)), \
                caplog.at_level(logging.ERROR, logger="bot"):
            assert bot._resolve_startup_prices() is None
        assert any("has an open position" in r.message for r in caplog.records)

    def test_zero_size_position_entry_does_not_block_skip(self):
        state = {"assetPositions": [_position("B", "0.0")]}
        bot = _make_bot(["xyz:A", "mkts:B"], {"xyz:A": 1.0, "mkts:B": None}, user_state=state)
        with patch("bot.Config", _config(SKIP_UNAVAILABLE_COINS=True)):
            assert bot._resolve_startup_prices() == ({"xyz:A": 1.0}, ["mkts:B"])

    def test_position_on_other_coin_does_not_block_skip(self):
        state = {"assetPositions": [_position("A", "1.0")]}
        bot = _make_bot(["xyz:A", "mkts:B"], {"xyz:A": 1.0, "mkts:B": None}, user_state=state)
        with patch("bot.Config", _config(SKIP_UNAVAILABLE_COINS=True)):
            assert bot._resolve_startup_prices() == ({"xyz:A": 1.0}, ["mkts:B"])

    def test_unconfirmed_position_state_prevents_skip(self):
        """An error response must not be read as "no position"."""
        bot = _make_bot(["xyz:A", "mkts:B"], {"xyz:A": 1.0, "mkts:B": None}, user_state={})
        with patch("bot.Config", _config(SKIP_UNAVAILABLE_COINS=True)):
            assert bot._resolve_startup_prices() is None

    def test_position_query_exception_prevents_skip(self):
        bot = _make_bot(["xyz:A", "mkts:B"], {"xyz:A": 1.0, "mkts:B": None})
        with patch("bot.Config", _config(SKIP_UNAVAILABLE_COINS=True)), \
                patch("rate_limiter.api_wrapper.call", side_effect=ConnectionError("down")):
            assert bot._resolve_startup_prices() is None

    def test_all_prices_missing_aborts_even_when_flag_on(self, caplog):
        bot = _make_bot(["xyz:A", "xyz:B"], {"xyz:A": None, "xyz:B": None})
        with patch("bot.Config", _config(SKIP_UNAVAILABLE_COINS=True)), \
                caplog.at_level(logging.ERROR, logger="bot"):
            assert bot._resolve_startup_prices() is None
        assert any("could not get market price for any coin" in r.message.lower() for r in caplog.records)
        bot.info.user_state.assert_not_called()

    def test_empty_coin_list_aborts(self, caplog):
        bot = _make_bot([], {})
        with patch("bot.Config", _config(SKIP_UNAVAILABLE_COINS=True)), \
                caplog.at_level(logging.ERROR, logger="bot"):
            assert bot._resolve_startup_prices() is None
        assert any("No tradable coins" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Wiring into _validate_trading_configuration
# ---------------------------------------------------------------------------

class TestValidationWiring:

    @pytest.fixture
    def validator(self):
        with patch("bot.MarginValidator") as cls:
            instance = cls.return_value
            instance.validate_minimum_requirements.return_value = SimpleNamespace(
                is_valid=True, message="", recommendations=[])
            instance.validate_strategy_config.return_value = SimpleNamespace(is_valid=True)
            yield instance

    def test_skipped_coin_is_removed_before_margin_validation(self, validator):
        bot = _make_bot(["xyz:A", "mkts:B", "xyz:C"], {"xyz:A": 1.0, "mkts:B": None, "xyz:C": 3.0})
        bot.strategy_name = "market_making"
        bot.strategy_config = {}

        with patch("bot.Config", _config(SKIP_UNAVAILABLE_COINS=True)):
            assert bot._validate_trading_configuration() is True

        assert bot.trading_coins == ["xyz:A", "xyz:C"]
        # The WS feed and strategy loop read self.coins after validation
        assert bot.coins == ["xyz:A", "xyz:C"]
        kwargs = validator.validate_strategy_config.call_args.kwargs
        assert kwargs["coins"] == ["xyz:A", "xyz:C"]
        assert kwargs["current_prices"] == {"xyz:A": 1.0, "xyz:C": 3.0}

    def test_missing_price_fails_validation_when_flag_off(self, validator):
        bot = _make_bot(["xyz:A", "mkts:B"], {"xyz:A": 1.0, "mkts:B": None})
        bot.strategy_name = "market_making"
        bot.strategy_config = {}

        with patch("bot.Config", _config()):
            assert bot._validate_trading_configuration() is False

        assert bot.trading_coins == ["xyz:A", "mkts:B"]
        validator.validate_strategy_config.assert_not_called()
