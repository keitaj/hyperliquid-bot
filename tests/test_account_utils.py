"""Unit tests for account_utils (no network required)."""

from typing import List, Optional
from unittest.mock import MagicMock, patch
import pytest

from account_utils import (
    get_account_snapshot, AccountSnapshot, _COLLATERAL_COINS,
    invalidate_snapshot_cache, set_hip3_dexes, get_hip3_dexes,
    abstraction_mismatch_warning, ABSTRACTION_STANDARD,
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
    dex_states: Optional[dict] = None,
):
    """Build a mock ``info`` object with controllable API responses.

    ``dex_states`` maps a DEX name to the ``user_state`` dict (or an exception
    to raise) returned for ``info.user_state(address, dex)``; the default
    clearinghouse (``dex=""``) keeps the standard response.
    """
    info = MagicMock()

    if user_state_override is not None:
        standard_state = user_state_override
    else:
        standard_state = {
            'marginSummary': {
                'accountValue': str(account_value),
                'totalMarginUsed': str(margin_used),
            },
        }

    if dex_states is None:
        info.user_state.return_value = standard_state
    else:
        def _user_state(address, dex=""):
            if not dex:
                return standard_state
            resp = dex_states[dex]
            if isinstance(resp, Exception):
                raise resp
            return resp
        info.user_state.side_effect = _user_state

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
    set_hip3_dexes(())
    with patch('account_utils.api_wrapper') as mock_wrapper:
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        yield mock_wrapper
    set_hip3_dexes(())
    invalidate_snapshot_cache()


def _dex_state(account_value: float, margin_used: float) -> dict:
    return {'marginSummary': {'accountValue': str(account_value), 'totalMarginUsed': str(margin_used)}}


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


class TestHip3DexEquity:
    """DEX clearinghouses are summed only when ``set_hip3_dexes`` was called."""

    def test_default_excludes_dex_clearinghouses(self):
        """Legacy behaviour: no DEX calls, DEX equity not counted."""
        info = _make_info(
            account_value=500.0, margin_used=100.0,
            dex_states={'xyz': _dex_state(300.0, 50.0)},
        )
        snap = get_account_snapshot(info, '0xabc')

        assert get_hip3_dexes() == ()
        assert snap.account_value == 500.0
        assert snap.margin_used == 100.0
        # only the standard clearinghouse was queried
        assert [c.args[1:] for c in info.user_state.call_args_list] == [()]

    def test_dex_equity_and_margin_summed(self):
        set_hip3_dexes(['xyz', 'km'])
        info = _make_info(
            account_value=500.0, margin_used=100.0,
            spot_balances=[{'coin': 'USDC', 'total': '200.0'}],
            dex_states={'xyz': _dex_state(300.0, 50.0), 'km': _dex_state(40.0, 10.0)},
        )
        snap = get_account_snapshot(info, '0xabc')

        assert snap.account_value == 1040.0   # 500 + 200 spot + 300 + 40
        assert snap.margin_used == 160.0      # 100 + 50 + 10
        dex_calls = [c.args[1] for c in info.user_state.call_args_list if len(c.args) > 1]
        assert dex_calls == ['xyz', 'km']

    def test_set_hip3_dexes_drops_empty_names_and_invalidates_cache(self):
        info = _make_info(account_value=500.0, dex_states={'xyz': _dex_state(300.0, 0.0)})
        assert get_account_snapshot(info, '0xabc').account_value == 500.0  # cached legacy value

        set_hip3_dexes(['', 'xyz'])
        assert get_hip3_dexes() == ('xyz',)
        # cache was invalidated: the next call re-fetches and includes xyz
        assert get_account_snapshot(info, '0xabc').account_value == 800.0

    def test_dex_api_failure_uses_last_known_balance(self):
        from rate_limiter import API_ERRORS
        set_hip3_dexes(['xyz'])
        info = _make_info(
            account_value=500.0, margin_used=100.0,
            dex_states={'xyz': API_ERRORS[0]('boom')},
        )
        snap = get_account_snapshot(info, '0xabc', last_known_balance=777.0)

        assert snap.account_value == 777.0
        assert snap.margin_used == 100.0

    def test_dex_api_failure_without_fallback_adds_nothing(self):
        """All-or-nothing: a failing DEX never yields a partial DEX sum."""
        from rate_limiter import API_ERRORS
        set_hip3_dexes(['xyz', 'km'])
        info = _make_info(
            account_value=500.0, margin_used=100.0,
            dex_states={'xyz': API_ERRORS[0]('boom'), 'km': _dex_state(40.0, 10.0)},
        )
        snap = get_account_snapshot(info, '0xabc')

        assert snap.account_value == 500.0
        assert snap.margin_used == 100.0

    def test_spot_fallback_does_not_double_count_dex_equity(self):
        """Blocker from review: last_known_balance is a complete prior snapshot
        (DEX equity included), so DEX equity must not be added on top of it."""
        from rate_limiter import API_ERRORS
        set_hip3_dexes(['xyz'])
        info = _make_info(
            account_value=500.0, margin_used=100.0,
            spot_raises=API_ERRORS[0]('spot down'),
            dex_states={'xyz': _dex_state(300.0, 50.0)},
        )
        snap = get_account_snapshot(info, '0xabc', last_known_balance=1000.0)

        assert snap.account_value == 1000.0           # not 1300
        # DEX was not even queried once the fallback value was substituted
        assert all(len(c.args) == 1 for c in info.user_state.call_args_list)

    def test_dex_totals_cached_on_prefetched_user_state_path(self):
        """risk_manager passes a pre-fetched user_state (bypassing the snapshot
        cache); the DEX calls must still be shared within the TTL."""
        set_hip3_dexes(['xyz'])
        info = _make_info(account_value=500.0, margin_used=100.0, dex_states={'xyz': _dex_state(300.0, 50.0)})
        pre = {'marginSummary': {'accountValue': '500.0', 'totalMarginUsed': '100.0'}}

        for _ in range(3):
            snap = get_account_snapshot(info, '0xabc', user_state=pre)
            assert snap.account_value == 800.0
            assert snap.margin_used == 150.0

        dex_calls = [c for c in info.user_state.call_args_list if len(c.args) > 1]
        assert len(dex_calls) == 1

    def test_invalidate_cache_also_drops_dex_totals(self):
        set_hip3_dexes(['xyz'])
        info = _make_info(account_value=500.0, dex_states={'xyz': _dex_state(300.0, 0.0)})
        pre = {'marginSummary': {'accountValue': '500.0', 'totalMarginUsed': '0'}}
        get_account_snapshot(info, '0xabc', user_state=pre)
        invalidate_snapshot_cache('0xabc')
        get_account_snapshot(info, '0xabc', user_state=pre)

        dex_calls = [c for c in info.user_state.call_args_list if len(c.args) > 1]
        assert len(dex_calls) == 2

    def test_dex_state_missing_margin_summary_counts_zero(self):
        set_hip3_dexes(['xyz'])
        info = _make_info(account_value=500.0, margin_used=100.0, dex_states={'xyz': {}})
        snap = get_account_snapshot(info, '0xabc')

        assert snap.account_value == 500.0
        assert snap.margin_used == 100.0


class TestAbstractionMismatchWarning:
    """The flag must agree with the account's abstraction mode."""

    @pytest.mark.parametrize("mode", ["unifiedAccount", "portfolioMargin"])
    def test_flag_on_under_shared_collateral_warns_double_count(self, mode):
        msg = abstraction_mismatch_warning(mode, True, ['xyz'])
        assert msg is not None and 'double count' in msg

    def test_flag_off_under_standard_warns_understated(self):
        msg = abstraction_mismatch_warning(ABSTRACTION_STANDARD, False, ['xyz'])
        assert msg is not None and 'understated' in msg

    @pytest.mark.parametrize("mode,flag", [
        (ABSTRACTION_STANDARD, True),       # standard + flag on: consistent
        ("unifiedAccount", False),          # unified + flag off: consistent (legacy)
        ("portfolioMargin", False),
    ])
    def test_consistent_combinations_return_none(self, mode, flag):
        assert abstraction_mismatch_warning(mode, flag, ['xyz']) is None

    @pytest.mark.parametrize("mode", [None, "default", "dexAbstraction"])
    def test_unknown_or_undocumented_modes_do_not_warn(self, mode):
        assert abstraction_mismatch_warning(mode, True, ['xyz']) is None
        assert abstraction_mismatch_warning(mode, False, ['xyz']) is None

    def test_no_dexes_never_warns(self):
        assert abstraction_mismatch_warning("unifiedAccount", True, []) is None
        assert abstraction_mismatch_warning(ABSTRACTION_STANDARD, False, []) is None


class TestCollateralCoins:
    """Verify the collateral coin set."""

    def test_expected_coins(self):
        assert _COLLATERAL_COINS == frozenset(('USDC', 'USDH', 'USDT0'))
