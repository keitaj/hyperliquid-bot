"""Tests for ``json_config_loader``.

The loader is a small surface-format adapter that produces a flat dict
ready to slot in between dataclass defaults and CLI/env overrides in
``HyperliquidBot.__init__``. These tests cover:

* flat vs nested form auto-detection
* layered file merge order (later files override earlier)
* unknown-key warnings (typo detection)
* fail modes (missing file, malformed JSON, non-object top-level)
"""

import json
import logging

import pytest

from json_config_loader import (
    ConfigError,
    _to_flat,
    _walk_nested,
    load_json_configs,
)


def _write(tmp_path, name: str, payload) -> str:
    """Helper: dump ``payload`` (dict or string) to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    if isinstance(payload, str):
        p.write_text(payload)
    else:
        p.write_text(json.dumps(payload))
    return str(p)


# --------------------------------------------------------------------------- #
# Flat form: keys passed through untouched
# --------------------------------------------------------------------------- #


class TestFlatForm:
    def test_top_level_scalars_pass_through(self, tmp_path):
        path = _write(tmp_path, "flat.json", {
            "refresh_tolerance_bp": 1,
            "forager_enabled": True,
            "spread_bps": 10,
        })
        out = load_json_configs([path])
        assert out == {
            "refresh_tolerance_bp": 1,
            "forager_enabled": True,
            "spread_bps": 10,
        }

    def test_metadata_keys_with_dollar_prefix_are_skipped(self, tmp_path):
        path = _write(tmp_path, "flat.json", {
            "$schema": "https://example.com/schema.json",
            "$comment": "explanatory note",
            "spread_bps": 10,
        })
        out = load_json_configs([path])
        assert out == {"spread_bps": 10}


# --------------------------------------------------------------------------- #
# Nested form: namespace-aware flattening
# --------------------------------------------------------------------------- #


class TestNestedForm:
    def test_market_making_namespace_strips_prefix(self, tmp_path):
        path = _write(tmp_path, "nested.json", {
            "market_making": {
                "spread_bps": 10,
                "refresh": {"tolerance_bp": 1, "max_age_seconds": 240},
                "forager": {"enabled": True, "score_threshold": 30.0},
            },
        })
        out = load_json_configs([path])
        assert out == {
            "spread_bps": 10,
            "refresh_tolerance_bp": 1,
            "refresh_max_age_seconds": 240,
            "forager_enabled": True,
            "forager_score_threshold": 30.0,
        }

    def test_deep_nesting_concatenates_path(self, tmp_path):
        path = _write(tmp_path, "nested.json", {
            "market_making": {
                "auto": {"exclude": {"threshold_bps": -3.0}},
            },
        })
        out = load_json_configs([path])
        # "auto.exclude.threshold_bps" → "auto_exclude_threshold_bps".
        assert out == {"auto_exclude_threshold_bps": -3.0}

    def test_risk_namespace_recognised(self, tmp_path):
        """``risk`` is one of the known namespaces and gets prefix-stripped."""
        path = _write(tmp_path, "nested.json", {
            "risk": {"daily_loss_limit": 200, "max_position_pct": 0.3},
        })
        out = load_json_configs([path])
        assert out == {"daily_loss_limit": 200, "max_position_pct": 0.3}

    def test_unknown_namespace_keeps_prefix_in_path(self, tmp_path):
        """An unrecognised namespace is still flattened (with prefix kept)
        so the validator can warn instead of silently dropping config."""
        path = _write(tmp_path, "nested.json", {
            "futures_v2": {"some_key": 42},
        })
        out = load_json_configs([path])
        # Prefix retained: ``futures_v2_some_key``.
        assert out == {"futures_v2_some_key": 42}

    def test_top_level_scalars_alongside_namespace(self, tmp_path):
        """Mixed: top-level convenience keys plus a market_making block."""
        path = _write(tmp_path, "mixed.json", {
            "spread_bps": 5,
            "market_making": {"forager": {"enabled": True}},
        })
        out = load_json_configs([path])
        assert out == {"spread_bps": 5, "forager_enabled": True}


# --------------------------------------------------------------------------- #
# Layering: later files override earlier
# --------------------------------------------------------------------------- #


class TestLayering:
    def test_second_file_overrides_first(self, tmp_path):
        a = _write(tmp_path, "a.json", {"spread_bps": 5, "order_size_usd": 100})
        b = _write(tmp_path, "b.json", {"spread_bps": 10})
        out = load_json_configs([a, b])
        assert out == {"spread_bps": 10, "order_size_usd": 100}

    def test_three_files_chain(self, tmp_path):
        a = _write(tmp_path, "a.json", {"x": 1, "y": 1})
        b = _write(tmp_path, "b.json", {"y": 2, "z": 2})
        c = _write(tmp_path, "c.json", {"z": 3})
        out = load_json_configs([a, b, c])
        assert out == {"x": 1, "y": 2, "z": 3}


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #


class TestFailureModes:
    def test_missing_file_warns_and_skips(self, tmp_path, caplog):
        existing = _write(tmp_path, "ok.json", {"spread_bps": 10})
        missing = str(tmp_path / "nope.json")
        with caplog.at_level(logging.WARNING):
            out = load_json_configs([missing, existing])
        assert out == {"spread_bps": 10}
        assert any("not found" in rec.message for rec in caplog.records)

    def test_malformed_json_raises_config_error(self, tmp_path):
        path = _write(tmp_path, "bad.json", "{ this is not valid")
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_json_configs([path])

    def test_non_object_top_level_raises(self, tmp_path):
        path = _write(tmp_path, "list.json", "[1, 2, 3]")
        with pytest.raises(ConfigError, match="must be an object"):
            load_json_configs([path])

    def test_no_paths_returns_empty(self):
        assert load_json_configs([]) == {}


# --------------------------------------------------------------------------- #
# Typo detection
# --------------------------------------------------------------------------- #


class TestTypoDetection:
    def test_unknown_key_warns_when_known_keys_supplied(self, tmp_path, caplog):
        path = _write(tmp_path, "typo.json", {
            "spread_bps": 10,
            "spreed_bps": 99,  # typo
            "frorager_enabled": True,  # typo
        })
        known = {"spread_bps", "forager_enabled"}
        with caplog.at_level(logging.WARNING):
            out = load_json_configs([path], known_keys=known)
        # All keys still emitted (caller chooses how strict).
        assert out["spread_bps"] == 10
        assert out["spreed_bps"] == 99
        assert out["frorager_enabled"] is True
        # But the warning identifies the typos.
        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Unknown keys" in m for m in warnings)
        # Sorted in the warning so the message is deterministic.
        assert any("frorager_enabled" in m and "spreed_bps" in m for m in warnings)

    def test_known_keys_none_disables_typo_warning(self, tmp_path, caplog):
        path = _write(tmp_path, "any.json", {"made_up_key": 1})
        with caplog.at_level(logging.WARNING):
            load_json_configs([path], known_keys=None)
        assert not any("Unknown keys" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# Internal helpers (white-box) — pin the underscore-concat semantics
# --------------------------------------------------------------------------- #


class TestWalkNested:
    def test_single_level(self):
        result = dict(_walk_nested({"a": 1, "b": "two"}))
        assert result == {"a": 1, "b": "two"}

    def test_two_levels(self):
        result = dict(_walk_nested({"refresh": {"tolerance_bp": 1, "max_age_seconds": 240}}))
        assert result == {"refresh_tolerance_bp": 1, "refresh_max_age_seconds": 240}

    def test_three_levels(self):
        result = dict(_walk_nested({"forager": {"weights": {"activity": 0.3}}}))
        assert result == {"forager_weights_activity": 0.3}

    def test_with_prefix(self):
        result = dict(_walk_nested({"x": 1}, prefix=["foo", "bar"]))
        assert result == {"foo_bar_x": 1}


class TestToFlat:
    def test_pure_flat_input(self):
        # When no recognised namespace exists at the top level, treat as flat.
        out = _to_flat({"a": 1, "b": 2}, strategy_name="market_making")
        assert out == {"a": 1, "b": 2}

    def test_pure_nested_input(self):
        out = _to_flat({"market_making": {"a": 1, "b": 2}}, strategy_name="market_making")
        assert out == {"a": 1, "b": 2}
