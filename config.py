import os
from typing import Optional
from dotenv import load_dotenv
from hyperliquid.utils import constants

load_dotenv()


def _parse_list(env_val: str) -> list:
    """Parse comma-separated env var into a list, filtering empty strings."""
    return [x.strip() for x in env_val.split(",") if x.strip()]


class Config:
    ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
    PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY")
    USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"

    API_URL = constants.TESTNET_API_URL if USE_TESTNET else constants.MAINNET_API_URL

    DEFAULT_SLIPPAGE = 0.1
    MAX_POSITION_SIZE = 1000

    # ------------------------------------------------------------------ #
    # HIP-3 Multi-DEX configuration
    # ------------------------------------------------------------------ #

    # Comma-separated list of HIP-3 DEX names to trade on.
    # Known names: xyz (trade.xyz), flx (Felix), cash (DreamCash), km (Markets by Kinetiq), vntl (Ventuals), hyna (HyENA)
    # Leave empty to trade only on standard Hyperliquid.
    # Example: TRADING_DEXES=xyz,flx
    TRADING_DEXES: list = _parse_list(os.getenv("TRADING_DEXES", ""))

    # Whether to also trade standard Hyperliquid perps (non-HIP-3).
    ENABLE_STANDARD_HL: bool = os.getenv("ENABLE_STANDARD_HL", "true").lower() == "true"

    # Per-DEX coin overrides.  If not set, the bot uses coins passed via CLI.
    # Format: comma-separated coin names WITHOUT the "dex:" prefix.
    # Example: XYZ_COINS=XYZ100,XYZ200
    DEX_COINS: dict = {}
    for _dex in TRADING_DEXES:
        _env_key = f"{_dex.upper()}_COINS"
        _val = os.getenv(_env_key, "")
        if _val:
            DEX_COINS[_dex] = _parse_list(_val)

    # ------------------------------------------------------------------ #
    # Risk guardrail configuration
    # ------------------------------------------------------------------ #

    # Max single position as a fraction of account value (0.0–1.0).
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.2"))

    # Stop opening new orders when margin usage exceeds this fraction.
    MAX_MARGIN_USAGE: float = float(os.getenv("MAX_MARGIN_USAGE", "0.8"))

    # Force close ALL positions when margin usage exceeds this fraction.
    # Disabled by default – set explicitly to enable.
    FORCE_CLOSE_MARGIN: Optional[float] = (
        float(os.getenv("FORCE_CLOSE_MARGIN"))
        if os.getenv("FORCE_CLOSE_MARGIN") is not None
        else None
    )

    # Absolute dollar daily loss that triggers an automatic bot stop.
    # Disabled by default – set explicitly to enable.
    DAILY_LOSS_LIMIT: Optional[float] = (
        float(os.getenv("DAILY_LOSS_LIMIT"))
        if os.getenv("DAILY_LOSS_LIMIT") is not None
        else None
    )

    # Cut losing trades at this percentage (0.0–1.0, e.g. 0.05 = 5%).
    # Disabled by default – set explicitly to enable.
    PER_TRADE_STOP_LOSS: Optional[float] = (
        float(os.getenv("PER_TRADE_STOP_LOSS"))
        if os.getenv("PER_TRADE_STOP_LOSS") is not None
        else None
    )

    # Maximum number of concurrent open positions.
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))

    # Seconds to wait after an emergency stop before resuming trading.
    COOLDOWN_AFTER_STOP: int = int(os.getenv("COOLDOWN_AFTER_STOP", "3600"))

    # Dynamic risk level: green (100%), yellow (50%), red (pause), black (close all).
    # Read at runtime directly from os.getenv("RISK_LEVEL") by RiskManager,
    # so it can be changed without restarting the bot.

    @classmethod
    def validate(cls):
        if not cls.ACCOUNT_ADDRESS:
            raise ValueError("HYPERLIQUID_ACCOUNT_ADDRESS not found in environment")
        if not cls.PRIVATE_KEY:
            raise ValueError("HYPERLIQUID_PRIVATE_KEY not found in environment")

        # Validate risk guardrail ranges
        if not 0.0 <= cls.MAX_POSITION_PCT <= 1.0:
            raise ValueError(f"MAX_POSITION_PCT must be 0.0–1.0, got {cls.MAX_POSITION_PCT}")
        if not 0.0 <= cls.MAX_MARGIN_USAGE <= 1.0:
            raise ValueError(f"MAX_MARGIN_USAGE must be 0.0–1.0, got {cls.MAX_MARGIN_USAGE}")
        if cls.FORCE_CLOSE_MARGIN is not None:
            if not 0.0 <= cls.FORCE_CLOSE_MARGIN <= 1.0:
                raise ValueError(f"FORCE_CLOSE_MARGIN must be 0.0–1.0, got {cls.FORCE_CLOSE_MARGIN}")
            if cls.FORCE_CLOSE_MARGIN < cls.MAX_MARGIN_USAGE:
                raise ValueError(
                    f"FORCE_CLOSE_MARGIN ({cls.FORCE_CLOSE_MARGIN}) must be >= "
                    f"MAX_MARGIN_USAGE ({cls.MAX_MARGIN_USAGE})"
                )
        if cls.DAILY_LOSS_LIMIT is not None and cls.DAILY_LOSS_LIMIT < 0:
            raise ValueError(f"DAILY_LOSS_LIMIT must be >= 0, got {cls.DAILY_LOSS_LIMIT}")
        if cls.PER_TRADE_STOP_LOSS is not None and not 0.0 <= cls.PER_TRADE_STOP_LOSS <= 1.0:
            raise ValueError(f"PER_TRADE_STOP_LOSS must be 0.0–1.0, got {cls.PER_TRADE_STOP_LOSS}")
        if cls.MAX_OPEN_POSITIONS < 1:
            raise ValueError(f"MAX_OPEN_POSITIONS must be >= 1, got {cls.MAX_OPEN_POSITIONS}")
