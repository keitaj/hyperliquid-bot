"""Shared account-value helpers for Portfolio Margin.

Consolidates the logic for fetching perp account value + spot stablecoin
collateral, which was previously duplicated across base_strategy,
risk_manager, and margin_validator.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from rate_limiter import api_wrapper, API_ERRORS

logger = logging.getLogger(__name__)

# Stablecoins that count as collateral under Portfolio Margin.
_COLLATERAL_COINS = frozenset(('USDC', 'USDH', 'USDT0'))

# Default TTL for the snapshot cache (seconds).
_DEFAULT_CACHE_TTL = 2.0


@dataclass
class AccountSnapshot:
    """Minimal account state shared by all callers."""
    account_value: float
    margin_used: float


# ------------------------------------------------------------------ #
# Module-level snapshot cache keyed by account address
# ------------------------------------------------------------------ #
_snapshot_cache: Dict[str, AccountSnapshot] = {}
_snapshot_cache_time: Dict[str, float] = {}
_snapshot_cache_ttl: float = _DEFAULT_CACHE_TTL


def set_snapshot_cache_ttl(ttl: float) -> None:
    """Override the snapshot cache TTL (seconds).  Called once at startup."""
    global _snapshot_cache_ttl
    _snapshot_cache_ttl = ttl


def invalidate_snapshot_cache(account_address: Optional[str] = None) -> None:
    """Drop cached snapshots.  If *account_address* is ``None``, drop all."""
    if account_address is None:
        _snapshot_cache.clear()
        _snapshot_cache_time.clear()
    else:
        _snapshot_cache.pop(account_address, None)
        _snapshot_cache_time.pop(account_address, None)


def get_account_snapshot(
    info: Any,
    account_address: str,
    *,
    last_known_balance: Optional[float] = None,
    user_state: Optional[Dict] = None,
) -> AccountSnapshot:
    """Fetch account value (perp + spot collateral) and margin used.

    Results are cached for ``_snapshot_cache_ttl`` seconds (default 2 s)
    so that multiple callers in the same bot cycle share one set of API
    calls instead of each fetching independently.

    Parameters
    ----------
    info :
        Hyperliquid ``Info`` instance.
    account_address :
        Wallet address.
    last_known_balance :
        If provided and the spot API fails, this value is used as a
        fallback for ``account_value`` to avoid false risk triggers.
    user_state :
        Pre-fetched ``user_state`` dict.  When supplied the function
        skips the ``info.user_state`` API call, avoiding a duplicate
        round-trip (useful when the caller already has the data).

    Returns
    -------
    AccountSnapshot
        ``account_value`` includes spot stablecoin balances (Portfolio Margin).
        ``margin_used`` comes from ``marginSummary.totalMarginUsed``.

    Raises
    ------
    ValueError
        If ``user_state`` or ``marginSummary`` is missing.
    """
    # ---- return cached snapshot if still fresh ----
    now = time.monotonic()
    if user_state is None:
        cached_time = _snapshot_cache_time.get(account_address, 0.0)
        if now - cached_time < _snapshot_cache_ttl and account_address in _snapshot_cache:
            return _snapshot_cache[account_address]

    # ---- fetch user_state if not provided ----
    if user_state is None:
        user_state = api_wrapper.call(info.user_state, account_address)
    if not user_state or 'marginSummary' not in user_state:
        raise ValueError("Could not retrieve marginSummary from user_state")

    margin_summary = user_state['marginSummary']
    account_value = float(margin_summary.get('accountValue', 0))
    margin_used = float(margin_summary.get('totalMarginUsed', 0))

    # Portfolio Margin: spot stablecoin balances count as collateral.
    try:
        spot_state = api_wrapper.call(info.spot_user_state, account_address)
        for bal in spot_state.get('balances', []):
            if bal.get('coin', '') in _COLLATERAL_COINS:
                account_value += float(bal.get('total', 0))
    except API_ERRORS as e:
        if last_known_balance is not None:
            account_value = last_known_balance
            logger.debug(
                "Spot API failed, using last known balance: $%.2f", account_value
            )
        else:
            logger.debug("Could not fetch spot state: %s", e)

    snapshot = AccountSnapshot(account_value=account_value, margin_used=margin_used)

    # ---- update cache ----
    _snapshot_cache[account_address] = snapshot
    _snapshot_cache_time[account_address] = now

    return snapshot
