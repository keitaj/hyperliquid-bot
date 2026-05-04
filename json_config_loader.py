"""JSON config file loader with flat / nested auto-detection.

The bot's per-strategy ``strategy_config`` is a flat ``Dict[str, Any]``
historically populated by argparse + env vars + hardcoded defaults.
This module adds JSON files as a parallel input layer, supporting:

* **flat** form: keys match the existing CLI / env names directly.
* **nested** form: hierarchical, mirrors the ``MMConfig`` dataclass tree.

A nested file is auto-flattened by underscore-concatenating the path
under the strategy namespace, so:

    {"market_making": {"refresh": {"tolerance_bp": 1}}}

becomes:

    {"refresh_tolerance_bp": 1}

— exactly the key downstream code (`bot.py`, `MMConfig.from_legacy_dict`)
already understands. The loader does not introduce a new schema; it
simply lifts a more-readable surface format on top of the existing
flat key namespace.

Layering precedence (highest wins):

    CLI args  >  env vars  >  JSON files  >  dataclass defaults

JSON is opt-in: with no ``--config`` flag and no ``BOT_CONFIG`` env
var, this module is never invoked and behaviour is unchanged from
prior releases.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# Top-level JSON keys that are recognised as strategy / risk namespaces.
# Values inside these sections are flattened with their key prefix
# stripped (the strategy name) and the rest joined by underscores.
_KNOWN_NAMESPACES: Tuple[str, ...] = ("market_making", "risk")


class ConfigError(Exception):
    """Raised when a JSON config is structurally invalid (e.g., parse error)."""


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def load_json_configs(
    paths: List[str],
    strategy_name: str = "market_making",
    known_keys: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Read and merge JSON config files in declaration order.

    Later paths override earlier ones (``dict.update`` semantics). Missing
    files emit a warning and are skipped (fail-safe so bot still starts on
    typo-ed paths). Parse errors raise :class:`ConfigError` — the caller
    decides whether to abort or fall back to CLI/env-only.

    Returns the merged flat dict ready to slot in between
    ``default_configs`` and ``strategy_config`` (env/CLI) in
    ``bot.py``'s layering chain.

    ``known_keys`` enables typo detection: when supplied, any flat key
    not present in the set produces a warning. Pass
    ``validation.strategy_validator.known_market_making_keys()`` here.
    """
    merged: Dict[str, Any] = {}
    for path in paths:
        p = Path(path)
        if not p.exists():
            logger.warning(f"[config] JSON file not found, skipping: {path}")
            continue
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            raise ConfigError(f"[config] Invalid JSON in {path}: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(
                f"[config] Top-level value in {path} must be an object, "
                f"got {type(data).__name__}"
            )
        flat = _to_flat(data, strategy_name)
        if known_keys is not None:
            unknown = sorted(set(flat) - known_keys)
            if unknown:
                logger.warning(
                    f"[config] Unknown keys in {path}: {unknown} "
                    f"(typo? unsupported feature?)"
                )
        merged.update(flat)
        logger.info(f"[config] Loaded {len(flat)} key(s) from {path}")
    return merged


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _to_flat(data: Dict[str, Any], strategy_name: str) -> Dict[str, Any]:
    """Normalise either flat or nested form into flat keys.

    Detection rule:

    * If the top level contains a known namespace key (e.g.
      ``market_making``) whose value is a dict, treat as nested and
      recursively flatten under that namespace.
    * Otherwise, treat the top-level dict as already flat.

    Nested form may also be partial: keys not under a known namespace
    are still emitted (with their nested path joined). This lets users
    use top-level convenience keys (e.g. ``"forager_enabled": true``)
    side-by-side with ``"market_making": {...}``.
    """
    result: Dict[str, Any] = {}

    for top_key, top_value in data.items():
        if top_key.startswith("$"):
            # Reserved for $schema and similar metadata — silently skip.
            continue
        if top_key in _KNOWN_NAMESPACES and isinstance(top_value, dict):
            for flat_key, value in _walk_nested(top_value):
                result[flat_key] = value
        elif isinstance(top_value, dict):
            # Unknown nested namespace — still flatten under the top_key
            # so the validator can warn the user. Avoids silently dropping
            # plausibly-intended config.
            for flat_key, value in _walk_nested(top_value, prefix=[top_key]):
                result[flat_key] = value
        else:
            # Top-level scalar — assume flat form.
            result[top_key] = top_value

    return result


def _walk_nested(
    data: Dict[str, Any],
    prefix: Optional[List[str]] = None,
) -> Iterator[Tuple[str, Any]]:
    """Yield ``(flat_key, value)`` pairs from a nested dict.

    Path components are joined with underscores so
    ``{"forager": {"enabled": true}}`` → ``("forager_enabled", True)``.
    Inner dicts recurse; lists / scalars are emitted as-is.
    """
    prefix = prefix or []
    for key, value in data.items():
        new_path = prefix + [key]
        if isinstance(value, dict):
            yield from _walk_nested(value, new_path)
        else:
            yield ("_".join(new_path), value)
