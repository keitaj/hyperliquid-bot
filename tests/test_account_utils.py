"""Unit tests for account_utils (no network required)."""

from typing import List, Optional
from unittest.mock import MagicMock, patch
import pytest

from account_utils import (
    get_account_snapshot, AccountSnapshot, _COLLATERAL_COINS,
    invalidate_snapshot_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_info(
    account_value: float = 1000.0,
    margin_used: float = 200.0,
    spot_balances: Optional[List] = None,
    spot_raises: Optional[Exception] = None,
    user_state_override: Optional[dict] = None,
):
    """Build a mock ``info`` object with controllable API responses."""
    info = MagicMock()

    if user_state_override is not None:
        info.user_state.return_value = user_state_override
    else:
        info.user_state.return_value = {
            'marginSummary': {
                'accountValue': str(account_value),
                'totalMarginUsed': str(margin_used),
            },
        }

    if spot_raises is not None:
        info.spot_user_state.side_effect = spot_raises
    else:
        info.spot_user_state.return_value = {
            'balances': spot_balances or [],
        }

    return info


# Make api_wrapper.call just forward to the real function so that our
# mock info methods are invoked normally.
@pytest.fixture(autouse=True)
def _bypass_api_wrapper():
    invalidate_snapshot_cache()
    with patch('account_utils.api_wrapper') as mock_wrapper:
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        yield mock_wrapper
    invalidate_snapshot_cache()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetAccountSnapshot:
    """Tests for get_account_snapshot()."""

    def test_basic_perp_only(self):
        """When there are no spot balances, account_value comes from perp only."""
        info = _make_info(account_value=500.0, margin_used=100.0)
        snap = get_account_snapshot(info, '0xabc')

        assert snap.account_value == 500.0
        assert snap.margin_used == 100.0

    def test_spot_collateral_added(self):
        """Spot stablecoin balances are added to account_value."""
        info = _make_info(
            account_value=500.0,
            margin_used=100.0,
            spot_balances=[
                {'coin': 'USDC', 'total': '200.0'},
                {'coin': 'USDH', 'total': '50.0'},
                {'coin': 'USDT0', 'total': '30.0'},
            ],
        )
        snap = get_account_snapshot(info, '0xabc')

        assert snap.account_value == 780.0  # 500 + 200 + 50 + 30
        assert snap.margin_used == 100.0

    def test_non_collateral_coins_ignored(self):
        """Non-stablecoin spot balances should NOT be included."""
        info = _make_info(
            account_value=500.0,
            margin_used=0.0,
            spot_balances=[
                {'coin': 'USDC', 'total': '100.0'},
                {'coin': 'ETH', 'total': '9999.0'},
                {'coin': 'BTC', 'total': '50000.0'},
            ],
        )
        snap = get_account_snapshot(info, '0xabc')

        assert snap.account_value == 600.0  # 500 + 100 only

    def test_spot_api_failure_with_fallback(self):
        """When spot API fails and last_known_balance is provided, use it."""
        info = _make_info(
            account_value=500.0,
            margin_used=100.0,
            spot_raises=ConnectionError("429 Too Many Requests"),
        )
        snap = get_account_snapshot(info, '0xabc', last_known_balance=750.0)

        assert snap.account_value == 750.0
        assert snap.margin_used == 100.0

    def test_spot_api_failure_without_fallback(self):
        """When spot API fails and no fallback, use perp value only."""
        info = _make_info(
            account_value=500.0,
            margin_used=100.0,
            spot_raises=ConnectionError("429"),
        )
        snap = get_account_snapshot(info, '0xabc')

        assert snap.account_value == 500.0

    def test_missing_margin_summary_raises(self):
        """Should raise ValueError when marginSummary is absent."""
        info = _make_info(user_state_override={'someOtherKey': {}})

        with pytest.raises(ValueError, match="marginSummary"):
            get_account_snapshot(info, '0xabc')

    def test_empty_user_state_raises(self):
        """Should raise ValueError when user_state is empty/None."""
        info = _make_info(user_state_override={})

        with pytest.raises(ValueError, match="marginSummary"):
            get_account_snapshot(info, '0xabc')

    def test_zero_account_value_with_spot(self):
        """Perp account at 0 but spot has collateral (Portfolio Margin)."""
        info = _make_info(
            account_value=0.0,
            margin_used=0.0,
            spot_balances=[
                {'coin': 'USDC', 'total': '1000.0'},
            ],
        )
        snap = get_account_snapshot(info, '0xabc')

        assert snap.account_value == 1000.0

    def test_returns_account_snapshot_dataclass(self):
        """Return type should be AccountSnapshot."""
        info = _make_info()
        snap = get_account_snapshot(info, '0xabc')

        assert isinstance(snap, AccountSnapshot)


class TestSnapshotCache:
    """Tests for the TTL-based snapshot cache."""

    def test_cache_avoids_duplicate_api_calls(self):
        """Second call within TTL should reuse the cached result."""
        info = _make_info(account_value=500.0, margin_used=100.0)
        snap1 = get_account_snapshot(info, '0xabc')
        snap2 = get_account_snapshot(info, '0xabc')

        assert snap1 == snap2
        # user_state should only be called once (cached on second call)
        assert info.user_state.call_count == 1
        assert info.spot_user_state.call_count == 1

    def test_invalidate_cache_forces_refetch(self):
        """After invalidation, the next call should hit the API again."""
        info = _make_info(account_value=500.0, margin_used=100.0)
        get_account_snapshot(info, '0xabc')
        invalidate_snapshot_cache('0xabc')
        get_account_snapshot(info, '0xabc')

        assert info.user_state.call_count == 2

    def test_pre_fetched_user_state_skips_api_call(self):
        """When user_state is passed, info.user_state should not be called."""
        info = _make_info(account_value=500.0, margin_used=100.0)
        pre_fetched = {
            'marginSummary': {
                'accountValue': '800.0',
                'totalMarginUsed': '150.0',
            },
        }
        snap = get_account_snapshot(info, '0xabc', user_state=pre_fetched)

        assert snap.account_value == 800.0
        assert snap.margin_used == 150.0
        assert info.user_state.call_count == 0

    def test_different_addresses_cached_independently(self):
        """Cache should be per-address."""
        info = _make_info(account_value=500.0, margin_used=100.0)
        get_account_snapshot(info, '0xabc')
        get_account_snapshot(info, '0xdef')

        assert info.user_state.call_count == 2


class TestCollateralCoins:
    """Verify the collateral coin set."""

    def test_expected_coins(self):
        assert _COLLATERAL_COINS == frozenset(('USDC', 'USDH', 'USDT0'))
