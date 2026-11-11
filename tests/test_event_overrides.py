"""Tests for date-keyed per-coin event overrides (COIN_EVENT_OVERRIDES).

The feature must be stateless and fail-open:
- entries only act on their exact UTC date (past dates are inert and only
  counted for a cleanup hint; future dates are inert today),
- malformed entries are skipped with a warning, never aborting startup,
- multipliers may only reduce exposure (0 < m <= 1.0),
- an exclusion that empties a DEX coin list must keep the key present so
  the bot does not fall back to "discover all coins on the DEX".
"""

import importlib
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from config import _parse_event_overrides
from strategies.market_making_strategy import MarketMakingStrategy
from strategies.mm_config import MMConfig

TODAY = date(2026, 11, 17)


class TestParseEventOverrides:
    def test_today_exclude_and_size(self):
        excluded, mult, past = _parse_event_overrides(
            "NVDA:2026-11-17:exclude,SP500:2026-11-17:size0.5", TODAY)
        assert excluded == {"NVDA"}
        assert mult == {"SP500": 0.5}
        assert past == 0

    def test_future_date_is_inert(self):
        excluded, mult, past = _parse_event_overrides(
            "NVDA:2026-11-18:exclude", TODAY)
        assert excluded == set() and mult == {} and past == 0

    def test_past_date_is_inert_but_counted(self):
        excluded, mult, past = _parse_event_overrides(
            "NVDA:2026-11-03:exclude,TSLA:2026-10-01:size0.5", TODAY)
        assert excluded == set() and mult == {}
        assert past == 2

    def test_malformed_entries_skipped(self):
        excluded, mult, past = _parse_event_overrides(
            "NVDA:exclude,"                      # missing date
            "NVDA:2026-13-45:exclude,"           # bad date
            "NVDA:2026-11-17:hide,"              # unknown action
            "NVDA:2026-11-17:size1.5,"           # multiplier > 1.0
            "NVDA:2026-11-17:size0,"             # multiplier <= 0
            "NVDA:2026-11-17:sizeX,"             # non-numeric multiplier
            "::,"                                # empty fields
            "GOOGL:2026-11-17:size0.25",         # the one valid entry
            TODAY)
        assert excluded == set()
        assert mult == {"GOOGL": 0.25}
        assert past == 0

    def test_duplicate_last_wins(self):
        excluded, mult, _ = _parse_event_overrides(
            "NVDA:2026-11-17:exclude,NVDA:2026-11-17:size0.5", TODAY)
        assert excluded == set() and mult == {"NVDA": 0.5}

        excluded, mult, _ = _parse_event_overrides(
            "NVDA:2026-11-17:size0.5,NVDA:2026-11-17:exclude", TODAY)
        assert excluded == {"NVDA"} and mult == {}

    def test_empty_input(self):
        assert _parse_event_overrides("", TODAY) == (set(), {}, 0)

    def test_bad_action_on_future_date_still_warned_not_counted(self):
        # validation happens regardless of date so typos surface early
        excluded, mult, past = _parse_event_overrides(
            "NVDA:2099-01-01:size2.0", TODAY)
        assert excluded == set() and mult == {} and past == 0

    def test_dex_prefixed_coin_is_skipped(self):
        """Bare names only (matching *_COINS); a prefixed entry is ignored
        without disturbing its neighbours."""
        excluded, mult, past = _parse_event_overrides(
            "xyz:NVDA:2026-11-17:exclude,SP500:2026-11-17:size0.5", TODAY)
        assert excluded == set()
        assert mult == {"SP500": 0.5}
        assert past == 0


class TestConfigDexCoinsFiltering:
    """Reload the config module with a controlled environment."""

    ENV_KEYS = ("TRADING_DEXES", "XYZ_COINS", "COIN_EVENT_OVERRIDES")

    def _reload_with(self, **env):
        self._saved = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(env)
        import config as config_module
        return importlib.reload(config_module)

    def teardown_method(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import config as config_module
        importlib.reload(config_module)

    @staticmethod
    def _today_and_tomorrow(coin: str, action: str) -> str:
        """Entries for both today and tomorrow (UTC) so a run that straddles
        00:00 UTC between building the env and reloading still matches."""
        today = datetime.now(timezone.utc).date()
        tomorrow = date.fromordinal(today.toordinal() + 1)
        return f"{coin}:{today.isoformat()}:{action},{coin}:{tomorrow.isoformat()}:{action}"

    def test_excluded_coin_removed_from_dex_coins(self):
        mod = self._reload_with(
            TRADING_DEXES="xyz",
            XYZ_COINS="NVDA,SP500,GOOGL",
            COIN_EVENT_OVERRIDES=self._today_and_tomorrow("NVDA", "exclude"),
        )
        assert mod.Config.DEX_COINS["xyz"] == ["SP500", "GOOGL"]
        assert mod.Config.EVENT_EXCLUDED_COINS == {"NVDA"}

    def test_exclude_all_keeps_key_with_empty_list(self):
        """Empty-after-filter must NOT look like 'key missing' (which would
        mean 'discover all coins on the DEX')."""
        mod = self._reload_with(
            TRADING_DEXES="xyz",
            XYZ_COINS="NVDA",
            COIN_EVENT_OVERRIDES=self._today_and_tomorrow("NVDA", "exclude"),
        )
        assert "xyz" in mod.Config.DEX_COINS
        assert mod.Config.DEX_COINS["xyz"] == []

    def test_non_matching_date_changes_nothing(self):
        mod = self._reload_with(
            TRADING_DEXES="xyz",
            XYZ_COINS="NVDA,SP500",
            COIN_EVENT_OVERRIDES="NVDA:2020-01-01:exclude",
        )
        assert mod.Config.DEX_COINS["xyz"] == ["NVDA", "SP500"]
        assert mod.Config.EVENT_EXCLUDED_COINS == set()
        assert mod.Config.EVENT_PAST_ENTRIES == 1

    def test_unset_env_is_fully_backward_compatible(self):
        mod = self._reload_with(
            TRADING_DEXES="xyz",
            XYZ_COINS="NVDA,SP500",
        )
        assert mod.Config.DEX_COINS["xyz"] == ["NVDA", "SP500"]
        assert mod.Config.EVENT_EXCLUDED_COINS == set()
        assert mod.Config.EVENT_SIZE_MULTIPLIERS == {}
        assert mod.Config.EVENT_PAST_ENTRIES == 0


class TestBotCoinListGuards:
    """bot.py helpers that stand between an event exclusion and the
    BTC/ETH default."""

    def test_resolve_keeps_configured_coins(self):
        from bot import HyperliquidBot
        assert HyperliquidBot._resolve_trading_coins(["xyz:NVDA"], [], False) == ["xyz:NVDA"]

    def test_resolve_empty_config_falls_back_to_majors(self):
        from bot import HyperliquidBot
        assert HyperliquidBot._resolve_trading_coins([], [], False) == ["BTC", "ETH"]

    def test_resolve_all_delisted_stays_empty(self):
        from bot import HyperliquidBot
        assert HyperliquidBot._resolve_trading_coins([], ["xyz:OLD"], False) == []

    def test_resolve_all_event_excluded_stays_empty(self):
        """Excluding every configured coin for the day must not open BTC/ETH."""
        from bot import HyperliquidBot
        assert HyperliquidBot._resolve_trading_coins([], [], True) == []

    def test_hl_coins_filtered_by_event_exclusions(self):
        from bot import HyperliquidBot
        assert HyperliquidBot._apply_event_exclusions(
            ["BTC", "ETH", "SOL"], {"ETH"}) == ["BTC", "SOL"]

    def test_hl_coins_untouched_without_exclusions(self):
        from bot import HyperliquidBot
        coins = ["BTC", "ETH"]
        assert HyperliquidBot._apply_event_exclusions(coins, set()) == ["BTC", "ETH"]


class TestMMConfigParsing:
    def test_dict_passthrough(self):
        cfg = MMConfig.from_legacy_dict(
            {"event_size_multipliers": {"NVDA": 0.5}})
        assert cfg.event_size_multipliers == {"NVDA": 0.5}

    def test_string_form_parsed(self):
        cfg = MMConfig.from_legacy_dict(
            {"event_size_multipliers": "NVDA:0.5,xyz:SP500:0.25"})
        assert cfg.event_size_multipliers == {"NVDA": 0.5, "xyz:SP500": 0.25}

    def test_absent_defaults_to_empty(self):
        assert MMConfig.from_legacy_dict({}).event_size_multipliers == {}


def _make_strategy(**extra):
    config = {
        "spread_bps": 10,
        "order_size_usd": 200,
        "max_positions": 8,
        "maker_only": True,
        "bbo_mode": True,
        "bbo_offset_bps": 1.0,
        **extra,
    }
    return MarketMakingStrategy(MagicMock(), MagicMock(), config)


class TestStrategySizing:
    def test_multiplier_applies_after_coin_override(self):
        s = _make_strategy(
            coin_size_overrides="NVDA:45",
            event_size_multipliers={"NVDA": 0.5},
        )
        assert s._get_coin_size("xyz:NVDA") == 22.5

    def test_multiplier_applies_to_default_size_without_override(self):
        s = _make_strategy(event_size_multipliers={"SP500": 0.25})
        assert s._get_coin_size("xyz:SP500") == 50.0  # 200 * 0.25

    def test_other_coins_unaffected(self):
        s = _make_strategy(
            coin_size_overrides="NVDA:45,GOOGL:120",
            event_size_multipliers={"NVDA": 0.5},
        )
        assert s._get_coin_size("xyz:GOOGL") == 120.0
        assert s._get_coin_size("xyz:XYZ100") == 200.0

    def test_no_event_day_is_bit_identical(self):
        s = _make_strategy(coin_size_overrides="NVDA:45")
        assert s._event_size_multipliers == {}
        assert s._get_coin_size("xyz:NVDA") == 45.0
        assert s._get_coin_size("xyz:SP500") == 200.0

    def test_full_name_multiplier_matches_prefixed_coin(self):
        s = _make_strategy(event_size_multipliers={"xyz:NVDA": 0.5})
        assert s._get_coin_size("xyz:NVDA") == 100.0  # 200 * 0.5
        # bare lookup on another dex must not match the prefixed key
        assert s._get_coin_size("km:NVDA") == 200.0
