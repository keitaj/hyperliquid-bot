"""Shared position-closing utilities.

Centralises the market-close logic used by both HyperliquidBot (risk-driven
closes) and BaseStrategy (TP/SL closes) so that the core
"round → market-order → log" pattern lives in exactly one place.
"""

import logging
from typing import Optional

from order_manager import OrderManager, OrderSide
from market_data import MarketDataManager

logger = logging.getLogger(__name__)


def close_position_market(
    coin: str,
    size: float,
    market_data: MarketDataManager,
    order_manager: OrderManager,
    *,
    reason: str = "",
) -> bool:
    """Market-close a position.

    Parameters
    ----------
    coin : str
        The asset symbol (e.g. ``"BTC"``).
    size : float
        *Signed* position size — positive for long, negative for short.
    market_data : MarketDataManager
        Used to round the size to the correct number of decimals.
    order_manager : OrderManager
        Used to place the market order.
    reason : str, optional
        If provided, prepended to the log message.

    Returns
    -------
    bool
        ``True`` if the close order was placed successfully.
    """
    if size == 0:
        return False

    close_side = OrderSide.SELL if size > 0 else OrderSide.BUY
    abs_size = market_data.round_size(coin, abs(size))

    order = order_manager.create_market_order(
        coin=coin,
        side=close_side,
        size=abs_size,
        reduce_only=True,
    )

    if order:
        prefix = f"{reason}: " if reason else ""
        logger.info(f"{prefix}Closed position for {coin}: size={abs_size}")
        return True

    logger.error(f"Failed to close position for {coin}")
    return False
