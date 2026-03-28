"""Shared utilities for HIP-3 coin notation.

HIP-3 coins use "dex:coin" format (e.g. "xyz:GOLD", "flx:NVDA").
Standard Hyperliquid coins are bare names (e.g. "BTC", "ETH").

All code that needs to detect, parse, or construct "dex:coin" strings
should use the helpers in this module to ensure consistent behaviour.
"""

from typing import Optional, Tuple


def is_hip3(coin: str) -> bool:
    """Return True if *coin* uses HIP-3 "dex:coin" format."""
    return ":" in coin


def parse_coin(coin: str) -> Tuple[Optional[str], str]:
    """Split a coin string into (dex, coin_name).

    "xyz:GOLD" → ("xyz", "GOLD")
    "BTC"      → (None, "BTC")
    """
    if ":" in coin:
        dex, coin_name = coin.split(":", 1)
        return dex, coin_name
    return None, coin


def make_hip3_coin(dex: str, coin_name: str) -> str:
    """Construct a "dex:coin" string, avoiding double-prefixing.

    make_hip3_coin("xyz", "GOLD")     → "xyz:GOLD"
    make_hip3_coin("xyz", "xyz:GOLD") → "xyz:GOLD"
    """
    if ":" in coin_name:
        return coin_name
    return f"{dex}:{coin_name}"
