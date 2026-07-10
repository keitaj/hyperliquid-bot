"""Wiring tests for per-coin close tiers + toxicity acceleration.

Covers the bot-level contracts the PositionCloser unit tests cannot see:
- the new config keys are registered in ``_STRATEGY_PARAMS`` so the CLI
  layer collects them into ``strategy_config``
- ``MarketMakingStrategy`` exposes ``_closer`` with ``set_adverse_tracker``
  (the duck-typed contract the bot.py injection block relies on via
  ``getattr(self.strategy, '_closer', None)``)
- flat config values flow through ``MMConfig.from_legacy_dict`` into the
  closer, and defaults leave the feature fully disabled
"""

from unittest.mock import MagicMock

import bot as bot_module
from strategies.market_making_strategy import MarketMakingStrategy

_NEW_KEYS = [
    'close_tier_min_seconds',
    'coin_close_tier_overrides',
    'close_tier_toxicity_enabled',
    'close_tier_toxicity_threshold_bps',
    'close_tier_toxicity_window',
    'close_tier_toxicity_multiplier',
    'close_tier_toxicity_min_fills',
    'close_tier_toxicity_floor_pct',
]


def _make_strategy(**extra):
    config = {
        'spread_bps': 10,
        'order_size_usd': 200,
        'max_open_orders': 4,
        'close_immediately': False,
        'max_positions': 8,
        'maker_only': True,
        **extra,
    }
    return MarketMakingStrategy(MagicMock(), MagicMock(), config)


class TestStrategyParamsRegistration:
    """New keys must be collected from CLI args into strategy_config."""

    def test_new_keys_registered_for_market_making(self):
        params = bot_module._STRATEGY_PARAMS['market_making']
        for key in _NEW_KEYS:
            assert key in params, f"{key} missing from _STRATEGY_PARAMS"


class TestCloserInjectionContract:
    """bot.py reaches the closer via getattr(strategy, '_closer', None)."""

    def test_strategy_exposes_closer_with_setter(self):
        strategy = _make_strategy()
        closer = getattr(strategy, '_closer', None)
        assert closer is not None
        assert callable(getattr(closer, 'set_adverse_tracker', None))

    def test_set_adverse_tracker_stores_tracker(self):
        strategy = _make_strategy()
        tracker = MagicMock()
        strategy._closer.set_adverse_tracker(tracker)
        assert strategy._closer._adverse_tracker is tracker


class TestConfigFlowsToCloser:
    """Flat dict -> MMConfig.from_legacy_dict -> PositionCloser."""

    def test_overrides_and_toxicity_reach_closer(self):
        strategy = _make_strategy(
            coin_close_tier_overrides='NVDA:0.30/0.55',
            close_tier_min_seconds=20.0,
            close_tier_toxicity_enabled=True,
            close_tier_toxicity_threshold_bps=-3.0,
        )
        closer = strategy._closer
        assert closer._coin_close_tier_overrides == {'NVDA': (0.30, 0.55)}
        assert closer.close_tier_min_seconds == 20.0
        assert closer._toxicity_config is not None
        assert closer._toxicity_config.enabled is True
        assert closer._toxicity_config.threshold_bps == -3.0

    def test_defaults_leave_feature_disabled(self):
        strategy = _make_strategy()
        closer = strategy._closer
        assert closer._coin_close_tier_overrides == {}
        assert closer.close_tier_min_seconds == 0.0
        assert closer._toxicity_config is not None
        assert closer._toxicity_config.enabled is False
        assert closer._adverse_tracker is None
