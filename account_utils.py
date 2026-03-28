"""Shared account-value helpers for Portfolio Margin.

Consolidates the logic for fetching perp account value + spot stablecoin
collateral, which was previously duplicated across base_strategy,
risk_manager, and margin_validator.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from rate_limiter import api_wrapper

logger = logging.getLogger(__name__)

# Stablecoins that count as collateral under Portfolio Margin.
_COLLATERAL_COINS = frozenset(('USDC', 'USDH', 'USDT0'))


@dataclass
class AccountSnapshot:
    """Minimal account state shared by all callers."""
    account_value: float
    margin_used: float


def get_account_snapshot(
    info: Any,
    account_address: str,
    *,
    last_known_balance: Optional[float] = None,
) -> AccountSnapshot:
    """Fetch account value (perp + spot collateral) and margin used.

    Parameters
    ----------
    info :
        Hyperliquid ``Info`` instance.
    account_address :
        Wallet address.
    last_known_balance :
        If provided and the spot API fails, this value is used as a
        fallback for ``account_value`` to avoid false risk triggers.

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
    except Exception as e:
        if last_known_balance is not None:
            account_value = last_known_balance
            logger.debug(
                "Spot API failed, using last known balance: $%.2f", account_value
            )
        else:
            logger.debug("Could not fetch spot state: %s", e)

    return AccountSnapshot(account_value=account_value, margin_used=margin_used)
