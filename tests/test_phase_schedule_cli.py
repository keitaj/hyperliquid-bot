"""Regression tests: market-calendar phase params reach the strategy.

The bug these pin (PR #175 → fix): the phase-schedule params were
JSON-config-only, but the farming deployment launches the bot from CLI
flags assembled by ``start_farming*.sh``. With no CLI flag and no entry
in ``_STRATEGY_PARAMS``, ``coin_market_overrides`` /
``phase_spread_multipliers`` never reached ``MMConfig.from_legacy_dict``, so the
feature was inert after "activation".

These tests verify both halves of the wiring:
1. the four config keys are registered in ``_STRATEGY_PARAMS``
2. each has a matching ``--flag`` whose argparse dest equals the key
3. the argparse-dest → strategy_config → from_dict path yields a live
   market calendar.
"""
import argparse
from pathlib import Path

import bot
from strategies.mm_config import MMConfig

PHASE_KEYS = [
    "coin_market_overrides",
    "phase_spread_multipliers",
    "phase_open_window_minutes",
    "phase_close_window_minutes",
]

_BOT_SRC = Path(bot.__file__).read_text()


def test_config_keys_registered_in_strategy_params():
    mm = bot._STRATEGY_PARAMS["market_making"]
    for key in PHASE_KEYS:
        assert key in mm, f"{key} missing from _STRATEGY_PARAMS['market_making']"


def test_cli_flags_exist_with_matching_dest():
    # argparse maps --coin-market-overrides -> dest coin_market_overrides.
    # A flag whose dest does not match its config key would silently fail
    # to populate strategy_config, which is exactly the shipped bug.
    for key in PHASE_KEYS:
        flag = "--" + key.replace("_", "-")
        assert f"add_argument('{flag}'" in _BOT_SRC, f"missing argparse flag {flag}"
        # confirm the auto-derived dest equals the config key
        parser = argparse.ArgumentParser()
        parser.add_argument(flag)
        ns = parser.parse_args([flag, "x"])
        assert hasattr(ns, key), f"{flag} dest does not resolve to {key}"


def _collect_like_bot(namespace, params):
    """Mirror bot.py's nested ``_collect_params`` (copy non-None args)."""
    dest = {}
    for entry in params:
        arg_name, config_key = entry if isinstance(entry, tuple) else (entry, entry)
        val = getattr(namespace, arg_name, None)
        if val is not None:
            dest[config_key] = val
    return dest


def test_cli_values_flow_into_live_market_calendar():
    # Simulate parsed CLI args carrying the phase schedule.
    ns = argparse.Namespace(
        coin_market_overrides="NVDA:NYSE,SP500:CME_INDEX",
        phase_spread_multipliers="closed:2.5,regular:2.0",
        phase_open_window_minutes=30.0,
        phase_close_window_minutes=30.0,
    )
    strategy_config = _collect_like_bot(ns, bot._STRATEGY_PARAMS["market_making"])
    for key in PHASE_KEYS:
        assert key in strategy_config

    cfg = MMConfig.from_legacy_dict(strategy_config)
    assert cfg.schedule.coin_markets == {"NVDA": "NYSE", "SP500": "CME_INDEX"}
    assert cfg.schedule.phase_spread_multipliers == {"closed": 2.5, "regular": 2.0}


def test_unset_phase_flags_leave_calendar_empty():
    # default=None for every phase flag means an un-flagged launch keeps
    # the legacy behaviour (empty calendar), not a half-configured one.
    ns = argparse.Namespace(
        coin_market_overrides="",
        phase_spread_multipliers="",
        phase_open_window_minutes=None,
        phase_close_window_minutes=None,
    )
    strategy_config = _collect_like_bot(ns, bot._STRATEGY_PARAMS["market_making"])
    cfg = MMConfig.from_legacy_dict(strategy_config)
    assert cfg.schedule.coin_markets == {}
    assert cfg.schedule.phase_spread_multipliers == {}
