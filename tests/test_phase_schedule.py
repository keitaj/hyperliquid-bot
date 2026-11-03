"""Tests for the per-coin market-calendar phase schedule wiring."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy
from strategies.mm_config import parse_coin_str_overrides, parse_phase_multipliers


def _make_strategy(**extra):
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
        **extra,
    }
    return MarketMakingStrategy(market_data, order_manager, config)


def _freeze(mock_dt, *args):
    mock_dt.now.return_value = datetime(*args, tzinfo=timezone.utc)
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)


# ── Parsers ──────────────────────────────────────────────────────────

class TestParsers:
    def test_coin_str_overrides(self):
        got = parse_coin_str_overrides("NVDA:NYSE, xyz:SP500:CME_INDEX,bad,empty:")
        assert got == {"NVDA": "NYSE", "xyz:SP500": "CME_INDEX"}

    def test_coin_str_overrides_empty(self):
        assert parse_coin_str_overrides("") == {}
        assert parse_coin_str_overrides(None) == {}

    def test_phase_multipliers_valid_and_invalid(self):
        got = parse_phase_multipliers("regular:1.5,closed:0,bogus:2,open_window:-1,lunch:x")
        # unknown phase, negative and non-numeric entries are skipped
        assert got == {"regular": 1.5, "closed": 0.0}


# ── Strategy wiring ──────────────────────────────────────────────────

class TestPhaseMultiplier:
    def test_unassigned_coin_returns_none(self):
        strategy = _make_strategy()
        assert strategy._phase_spread_multiplier("xyz:SP500") is None

    @patch('strategies.market_making_strategy.datetime')
    def test_assigned_coin_regular_multiplier(self, mock_dt):
        # Wed 2026-11-04 16:00 UTC = 11:00 EST -> regular (US winter time).
        _freeze(mock_dt, 2026, 11, 4, 16, 0)
        strategy = _make_strategy(
            coin_market_overrides="NVDA:NYSE",
            phase_spread_multipliers="regular:1.5,closed:0",
        )
        assert strategy._phase_spread_multiplier("xyz:NVDA") == 1.5

    @patch('strategies.market_making_strategy.datetime')
    def test_closed_phase_zero_suppresses(self, mock_dt):
        # 03:00 UTC = 22:00 EST previous evening -> closed.
        _freeze(mock_dt, 2026, 11, 4, 3, 0)
        strategy = _make_strategy(
            coin_market_overrides="NVDA:NYSE",
            phase_spread_multipliers="regular:1.5,closed:0",
        )
        assert strategy._quote_suppression_reason("xyz:NVDA") == "phase_closed"

    @patch('strategies.market_making_strategy.datetime')
    def test_unlisted_phase_defaults_to_one(self, mock_dt):
        _freeze(mock_dt, 2026, 11, 4, 16, 0)
        strategy = _make_strategy(
            coin_market_overrides="NVDA:NYSE",
            phase_spread_multipliers="closed:2.0",
        )
        assert strategy._phase_spread_multiplier("xyz:NVDA") == 1.0

    def test_unknown_market_falls_back_to_legacy(self):
        strategy = _make_strategy(
            coin_market_overrides="NVDA:NARNIA",
            phase_spread_multipliers="regular:1.5",
        )
        assert strategy._coin_markets == {}
        assert strategy._phase_spread_multiplier("xyz:NVDA") is None

    @patch('strategies.market_making_strategy.datetime')
    def test_bare_name_matches_prefixed_coin(self, mock_dt):
        _freeze(mock_dt, 2026, 11, 4, 16, 0)
        strategy = _make_strategy(
            coin_market_overrides="NVDA:NYSE",
            phase_spread_multipliers="regular:2.5",
        )
        assert strategy._market_for("xyz:NVDA") == "NYSE"
        assert strategy._phase_spread_multiplier("xyz:NVDA") == 2.5

    @patch('strategies.market_making_strategy.datetime')
    def test_legacy_quiet_ignored_for_assigned_coin(self, mock_dt):
        # 17:00 UTC is a legacy quiet hour but NVDA is assigned -> no
        # suppression via quiet hours; NYSE 12:00 EST -> regular.
        _freeze(mock_dt, 2026, 11, 4, 17, 0)
        strategy = _make_strategy(
            coin_market_overrides="NVDA:NYSE",
            phase_spread_multipliers="regular:1.0",
            quiet_hours_utc="17",
        )
        assert strategy._quote_suppression_reason("xyz:NVDA") is None

    @patch('strategies.market_making_strategy.datetime')
    def test_mixed_deployment_legacy_coin_quiet(self, mock_dt):
        # In a mixed deployment the legacy coin is suppressed per coin
        # during a global quiet hour (the early-return path is skipped).
        _freeze(mock_dt, 2026, 11, 4, 17, 0)
        strategy = _make_strategy(
            coin_market_overrides="NVDA:NYSE",
            phase_spread_multipliers="regular:1.0",
            quiet_hours_utc="17",
        )
        assert strategy._quote_suppression_reason("xyz:SP500") == "quiet_hour"

    @patch('strategies.market_making_strategy.datetime')
    def test_phase_transition_logged_once(self, mock_dt):
        _freeze(mock_dt, 2026, 11, 4, 16, 0)
        strategy = _make_strategy(
            coin_market_overrides="NVDA:NYSE",
            phase_spread_multipliers="regular:1.0",
        )
        strategy._phase_for("xyz:NVDA")
        assert strategy._phase_state["xyz:NVDA"] == "regular"
