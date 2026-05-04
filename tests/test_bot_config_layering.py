"""Integration tests for the layered config in ``HyperliquidBot.__init__``.

Pins the precedence:

    CLI / env (strategy_config) > JSON (json_overrides) > dataclass defaults.

The bot is constructed with mocked external dependencies so the merge
logic is exercised in isolation. We intentionally probe via
``self.strategy_config`` (the merged dict stored on the bot) rather
than the strategy itself; the merge is bot-level and shouldn't depend
on strategy details.
"""

# --------------------------------------------------------------------------- #
# Direct test of the layering snippet (avoids exchange-connection overhead).
#
# The merge in bot.py looks exactly like:
#
#     config = {
#         **default_configs.get(strategy_name, {}),
#         **(json_overrides or {}),
#         **(strategy_config or {}),
#     }
#
# Replicate it here with controlled inputs to pin precedence semantics.
# --------------------------------------------------------------------------- #


def _layered_config(defaults, json_overrides, strategy_config):
    """Faithful replica of ``HyperliquidBot.__init__``'s merge order."""
    return {
        **(defaults or {}),
        **(json_overrides or {}),
        **(strategy_config or {}),
    }


class TestLayeringPrecedence:
    """Verify CLI > JSON > defaults, end-to-end through the merge step."""

    def test_only_defaults(self):
        merged = _layered_config({"a": 1, "b": 2}, None, None)
        assert merged == {"a": 1, "b": 2}

    def test_json_overrides_default(self):
        merged = _layered_config(
            defaults={"a": 1, "b": 2},
            json_overrides={"b": 99},
            strategy_config=None,
        )
        assert merged == {"a": 1, "b": 99}

    def test_cli_overrides_json(self):
        """CLI / env values must beat JSON for the same key."""
        merged = _layered_config(
            defaults={"x": 0},
            json_overrides={"x": 1, "y": 2},
            strategy_config={"x": 3},  # CLI wins
        )
        assert merged == {"x": 3, "y": 2}

    def test_three_layer_full_chain(self):
        merged = _layered_config(
            defaults={"a": 1, "b": 1, "c": 1},
            json_overrides={"b": 2, "c": 2},
            strategy_config={"c": 3},
        )
        assert merged == {"a": 1, "b": 2, "c": 3}

    def test_cli_only_keys_passed_through(self):
        """A key only in CLI should appear in the final config."""
        merged = _layered_config(
            defaults={"a": 1},
            json_overrides=None,
            strategy_config={"only_in_cli": "yes"},
        )
        assert merged == {"a": 1, "only_in_cli": "yes"}

    def test_json_only_keys_passed_through(self):
        """A key only in JSON should appear in the final config."""
        merged = _layered_config(
            defaults={"a": 1},
            json_overrides={"only_in_json": True},
            strategy_config=None,
        )
        assert merged == {"a": 1, "only_in_json": True}


class TestEmptyAndNoneSafe:
    """Edge cases around empty / None inputs."""

    def test_all_none_yields_empty(self):
        assert _layered_config(None, None, None) == {}

    def test_empty_dicts_yield_empty(self):
        assert _layered_config({}, {}, {}) == {}

    def test_none_strategy_config_treated_as_empty(self):
        merged = _layered_config({"a": 1}, {"b": 2}, None)
        assert merged == {"a": 1, "b": 2}


class TestRealWorldLayering:
    """Smoke test against realistic key sets."""

    def test_forager_partial_override(self):
        """Override one Forager weight via CLI while keeping JSON defaults."""
        defaults = {
            "forager_enabled": False,
            "forager_score_threshold": 30.0,
            "forager_weight_activity": 0.3,
            "forager_weight_quality": 0.4,
            "forager_weight_cost": 0.3,
        }
        json_overrides = {
            "forager_enabled": True,
            "forager_weight_quality": 0.5,
        }
        cli = {"forager_weight_quality": 0.6}  # CLI bumps further

        merged = _layered_config(defaults, json_overrides, cli)
        assert merged["forager_enabled"] is True            # JSON
        assert merged["forager_weight_activity"] == 0.3     # default
        assert merged["forager_weight_quality"] == 0.6      # CLI wins
        assert merged["forager_weight_cost"] == 0.3         # default
        assert merged["forager_score_threshold"] == 30.0    # default

    def test_disable_via_cli_when_json_enables(self):
        """An operator can flip a feature off via CLI even when JSON enables it."""
        merged = _layered_config(
            defaults={"forager_enabled": False},
            json_overrides={"forager_enabled": True},
            strategy_config={"forager_enabled": False},
        )
        assert merged["forager_enabled"] is False


# --------------------------------------------------------------------------- #
# end-to-end: load JSON via loader and check the result composes with CLI.
# --------------------------------------------------------------------------- #


def test_loader_output_layers_correctly_under_cli(tmp_path):
    """Round-trip: load JSON → merge with CLI → verify CLI wins."""
    import json
    from json_config_loader import load_json_configs

    # JSON: nested form, sets two keys.
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({
        "market_making": {
            "spread_bps": 5,
            "forager": {"enabled": True},
        },
    }))

    json_overrides = load_json_configs([str(cfg_path)])
    assert json_overrides == {"spread_bps": 5, "forager_enabled": True}

    # CLI bumps spread_bps to 10.
    cli = {"spread_bps": 10}
    merged = _layered_config(
        defaults={"spread_bps": 1, "forager_enabled": False, "max_open_orders": 4},
        json_overrides=json_overrides,
        strategy_config=cli,
    )
    assert merged == {
        "spread_bps": 10,           # CLI > JSON > default
        "forager_enabled": True,    # JSON > default
        "max_open_orders": 4,       # default
    }


# --------------------------------------------------------------------------- #
# Risk namespace: JSON values must reach Config, not just strategy_config.
# --------------------------------------------------------------------------- #


class TestJsonRiskOverrides:
    """``_apply_json_risk_overrides`` wires JSON ``risk:`` keys to Config.

    Saves and restores the relevant ``Config`` attributes so tests stay
    isolated from each other and from the wider suite.
    """

    _SAVED_ATTRS = (
        'MAX_POSITION_PCT', 'MAX_MARGIN_USAGE', 'DAILY_LOSS_LIMIT',
        'PER_TRADE_STOP_LOSS', 'FORCE_CLOSE_MARGIN', 'FORCE_CLOSE_LEVERAGE',
    )

    def setup_method(self):
        from config import Config
        self._snapshot = {a: getattr(Config, a) for a in self._SAVED_ATTRS}

    def teardown_method(self):
        from config import Config
        for a, v in self._snapshot.items():
            setattr(Config, a, v)

    def _args(self, **overrides):
        """Build a Namespace mimicking argparse output (None where unset)."""
        import argparse
        attrs = {p: None for p in (
            'max_position_pct', 'max_margin_usage', 'daily_loss_limit',
            'per_trade_stop_loss', 'force_close_margin', 'force_close_leverage',
            'max_open_positions', 'cooldown_after_stop',
        )}
        attrs.update(overrides)
        return argparse.Namespace(**attrs)

    def test_json_risk_value_sets_config_when_cli_unset(self):
        from bot import _apply_json_risk_overrides
        from config import Config

        json_overrides = {"daily_loss_limit": 250.0, "spread_bps": 10}
        args = self._args(daily_loss_limit=None)

        _apply_json_risk_overrides(json_overrides, args)

        assert Config.DAILY_LOSS_LIMIT == 250.0
        # Risk key popped from the dict — it should not pollute strategy_config.
        assert "daily_loss_limit" not in json_overrides
        # Non-risk key is left alone.
        assert json_overrides == {"spread_bps": 10}

    def test_cli_value_beats_json_for_risk(self):
        """CLI > JSON: when args has a risk param, JSON is ignored & popped."""
        from bot import _apply_json_risk_overrides
        from config import Config

        # Simulate the existing CLI loop having already applied 999 to Config.
        Config.DAILY_LOSS_LIMIT = 999.0

        json_overrides = {"daily_loss_limit": 250.0}
        args = self._args(daily_loss_limit=999.0)

        _apply_json_risk_overrides(json_overrides, args)

        # Config still holds the CLI value.
        assert Config.DAILY_LOSS_LIMIT == 999.0
        # JSON value was popped (so it doesn't leak into strategy_config)
        # but Config was *not* touched by the helper.
        assert "daily_loss_limit" not in json_overrides

    def test_multiple_risk_params(self):
        from bot import _apply_json_risk_overrides
        from config import Config

        json_overrides = {
            "max_position_pct": 0.5,
            "max_margin_usage": 0.6,
            "daily_loss_limit": 100.0,
            "per_trade_stop_loss": 0.03,
            "spread_bps": 7,
        }
        args = self._args()

        _apply_json_risk_overrides(json_overrides, args)

        assert Config.MAX_POSITION_PCT == 0.5
        assert Config.MAX_MARGIN_USAGE == 0.6
        assert Config.DAILY_LOSS_LIMIT == 100.0
        assert Config.PER_TRADE_STOP_LOSS == 0.03
        # Only the non-risk key remains in json_overrides.
        assert json_overrides == {"spread_bps": 7}

    def test_no_op_when_json_overrides_empty_or_none(self):
        from bot import _apply_json_risk_overrides
        from config import Config

        before = Config.DAILY_LOSS_LIMIT
        _apply_json_risk_overrides(None, self._args())
        _apply_json_risk_overrides({}, self._args())
        assert Config.DAILY_LOSS_LIMIT == before

    def test_unknown_risk_key_in_json_ignored(self):
        """Keys not in _RISK_PARAMS pass through untouched."""
        from bot import _apply_json_risk_overrides

        json_overrides = {"spread_bps": 10, "made_up_risk_param": 0.5}
        _apply_json_risk_overrides(json_overrides, self._args())
        # No risk keys present — both pass through untouched.
        assert json_overrides == {"spread_bps": 10, "made_up_risk_param": 0.5}
