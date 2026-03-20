import os
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
    # Known names: xyz (trade.xyz), flx (Felix), cash (DreamCash)
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

    @classmethod
    def validate(cls):
        if not cls.ACCOUNT_ADDRESS:
            raise ValueError("HYPERLIQUID_ACCOUNT_ADDRESS not found in environment")
        if not cls.PRIVATE_KEY:
            raise ValueError("HYPERLIQUID_PRIVATE_KEY not found in environment")