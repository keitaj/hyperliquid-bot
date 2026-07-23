"""Fill-time feature computation shared between logging and future inference.

``compute_fill_features`` is the single source of truth for the order-book
feature definitions used by the fill feature JSONL log
(:mod:`ws.fill_feature_writer`) and, in the future, by any in-bot inference
that consumes the same features (e.g. a toxicity score). Keeping the
definition in one pure function prevents train/serve skew: training
pipelines read the JSONL produced from this function, and inference builds
its input vector from the same function's output.

This module is observation-only — it has no trading side effects.
"""

from datetime import datetime
from typing import Any, Dict, Optional

# Bump when the feature set / semantics change so downstream consumers
# (training pipelines, coefficient files) can detect incompatibilities.
# v2: added realized_vol_bps.
FEATURE_SCHEMA_VERSION = 2


def compute_fill_features(
    md: Any,
    utc_now: datetime,
    realized_vol_bps: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """Compute fill-time order book features from a ``MarketData`` snapshot.

    Parameters
    ----------
    md : MarketData
        Snapshot with ``mid_price > 0`` — callers must validate before
        calling (mirrors the guard in ``AdverseSelectionTracker.on_fill``).
    utc_now : datetime
        Current UTC time, used for the ``utc_hour`` feature.
    realized_vol_bps : float, optional
        Recent per-coin realized volatility (bps), computed by the strategy
        and published to the tracker each cycle. ``None`` when unavailable
        (insufficient price history / feature unwired). Same definition is
        used by future inference, so passing it in here (rather than
        recomputing) preserves the single-source-of-truth / no train-serve
        skew property.

    Returns
    -------
    Dict[str, Optional[float]]
        Feature name -> value. Undefined features are ``None``.
    """
    mid = md.mid_price
    micro_price = getattr(md, 'micro_price', 0.0)
    return {
        "mid": mid,
        "spread_bps": md.spread / mid * 10_000,
        "book_imbalance": getattr(md, 'book_imbalance', 0.0),
        "micro_price_skew_bps": (
            (micro_price - mid) / mid * 10_000 if micro_price > 0 else None
        ),
        "bid_sz": getattr(md, 'bid_size_top', 0.0),
        "ask_sz": getattr(md, 'ask_size_top', 0.0),
        "utc_hour": utc_now.hour,
        "realized_vol_bps": realized_vol_bps,
        # Reserved slot: populated once an oracle price feed is wired in.
        "oracle_divergence_bps": None,
    }
