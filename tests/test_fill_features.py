"""Tests for fill_features.compute_fill_features — shared feature definitions."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from fill_features import FEATURE_SCHEMA_VERSION, compute_fill_features


def _make_md(
    mid_price: float = 100.0,
    spread: float = 0.05,
    book_imbalance: float = -0.25,
    bid_size_top: float = 12.0,
    ask_size_top: float = 18.0,
    micro_price: float = 99.98,
):
    md = MagicMock()
    md.mid_price = mid_price
    md.spread = spread
    md.book_imbalance = book_imbalance
    md.bid_size_top = bid_size_top
    md.ask_size_top = ask_size_top
    md.micro_price = micro_price
    return md


class TestComputeFillFeatures:
    def test_schema_version_is_pinned(self):
        assert FEATURE_SCHEMA_VERSION == 1

    def test_computes_all_features(self):
        md = _make_md()
        utc_now = datetime(2026, 7, 8, 14, 30, tzinfo=timezone.utc)

        features = compute_fill_features(md, utc_now)

        assert features["mid"] == 100.0
        # spread_bps = 0.05 / 100 * 10_000 = 5.0
        assert features["spread_bps"] == pytest.approx(5.0)
        assert features["book_imbalance"] == -0.25
        # micro_price_skew_bps = (99.98 - 100) / 100 * 10_000 = -2.0
        assert features["micro_price_skew_bps"] == pytest.approx(-2.0)
        assert features["bid_sz"] == 12.0
        assert features["ask_sz"] == 18.0
        assert features["utc_hour"] == 14

    def test_micro_price_skew_sign(self):
        """micro_price above mid -> positive skew (buy pressure)."""
        md = _make_md(mid_price=200.0, micro_price=200.04)
        features = compute_fill_features(md, datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert features["micro_price_skew_bps"] == pytest.approx(2.0)

    def test_micro_price_zero_yields_none(self):
        md = _make_md(micro_price=0.0)
        features = compute_fill_features(md, datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert features["micro_price_skew_bps"] is None

    def test_oracle_divergence_reserved_as_none(self):
        md = _make_md()
        features = compute_fill_features(md, datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert features["oracle_divergence_bps"] is None

    def test_utc_hour_boundaries(self):
        md = _make_md()
        for hour in (0, 23):
            utc_now = datetime(2026, 7, 8, hour, 59, tzinfo=timezone.utc)
            assert compute_fill_features(md, utc_now)["utc_hour"] == hour
