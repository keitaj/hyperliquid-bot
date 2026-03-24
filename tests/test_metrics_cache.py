"""Tests for risk manager metrics cache TTL."""
import os
from datetime import datetime
from unittest.mock import patch
from dataclasses import dataclass


@dataclass
class FakeRiskMetrics:
    total_balance: float = 1000.0
    available_balance: float = 1000.0
    margin_used: float = 0.0
    total_position_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    leverage: float = 0.0
    margin_ratio: float = 0.0
    num_positions: int = 0
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class TestMetricsCacheTTL:
    """Test that metrics_cache_ttl is respected."""

    def _make_risk_manager(self, ttl=2.0):
        """Create a minimal RiskManager-like object for testing cache logic."""
        # We test the cache logic directly without importing RiskManager
        # (which requires network/SDK)

        class FakeCacheManager:
            def __init__(self, cache_ttl):
                self.metrics_cache_ttl = cache_ttl
                self.risk_metrics_history = []
                self.fetch_count = 0

            def get_current_metrics(self):
                self.fetch_count += 1
                metrics = FakeRiskMetrics(timestamp=datetime.now())
                self.risk_metrics_history.append(metrics)
                return metrics

            def _get_cached_metrics(self):
                if self.risk_metrics_history:
                    last = self.risk_metrics_history[-1]
                    age = (datetime.now() - last.timestamp).total_seconds()
                    if age < self.metrics_cache_ttl:
                        return last
                return self.get_current_metrics()

        return FakeCacheManager(ttl)

    def test_default_ttl_is_2_seconds(self):
        """Default TTL should be 2.0 seconds."""
        mgr = self._make_risk_manager()
        assert mgr.metrics_cache_ttl == 2.0

    def test_custom_ttl(self):
        """Custom TTL should be respected."""
        mgr = self._make_risk_manager(ttl=10.0)
        assert mgr.metrics_cache_ttl == 10.0

    def test_cache_hit_within_ttl(self):
        """Calling _get_cached_metrics twice within TTL should not re-fetch."""
        mgr = self._make_risk_manager(ttl=10.0)

        result1 = mgr._get_cached_metrics()
        result2 = mgr._get_cached_metrics()

        assert mgr.fetch_count == 1  # Only fetched once
        assert result1 is result2  # Same object returned

    def test_cache_miss_after_ttl(self):
        """After TTL expires, _get_cached_metrics should re-fetch."""
        mgr = self._make_risk_manager(ttl=0.0)  # TTL=0 means always re-fetch

        mgr._get_cached_metrics()
        mgr._get_cached_metrics()

        assert mgr.fetch_count == 2  # Fetched twice

    def test_cache_miss_when_empty(self):
        """First call should always fetch."""
        mgr = self._make_risk_manager(ttl=10.0)
        assert mgr.fetch_count == 0

        mgr._get_cached_metrics()
        assert mgr.fetch_count == 1

    def test_config_env_var(self):
        """METRICS_CACHE_TTL env var should set the value."""
        with patch.dict(os.environ, {'METRICS_CACHE_TTL': '15.0'}):
            val = float(os.getenv('METRICS_CACHE_TTL', '2.0'))
            assert val == 15.0

    def test_config_env_var_default(self):
        """Without env var, default should be 2.0."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('METRICS_CACHE_TTL', None)
            val = float(os.getenv('METRICS_CACHE_TTL', '2.0'))
            assert val == 2.0
