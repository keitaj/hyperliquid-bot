import logging
import os
from datetime import date, datetime, timezone
from typing import Dict, Optional, Set, Tuple
from dotenv import load_dotenv
from hyperliquid.utils import constants

load_dotenv()

logger = logging.getLogger(__name__)


def _parse_list(env_val: str) -> list:
    """Parse comma-separated env var into a list, filtering empty strings."""
    return [x.strip() for x in env_val.split(",") if x.strip()]


def _parse_event_overrides(
    raw: str, today: date
) -> Tuple[Set[str], Dict[str, float], int]:
    """Parse ``COIN_EVENT_OVERRIDES`` into today's event actions.

    Format: comma-separated ``COIN:YYYY-MM-DD:ACTION`` entries where ACTION
    is ``exclude`` (drop the coin from trading for that UTC date) or
    ``size<multiplier>`` (scale the coin's effective order size; the
    multiplier must satisfy ``0 < m <= 1.0`` — event overrides may only
    reduce exposure, never raise it).

    Returns ``(excluded_today, size_multipliers_today, past_entry_count)``.
    Entries dated in the future are inert today; past-dated entries are
    counted so startup logging can suggest cleanup. For duplicate
    ``(coin, date)`` entries the last one wins.

    A malformed entry is skipped with a warning instead of aborting
    startup: refusing to start would turn a typo into an outage loop,
    while a silently-skipped protection is still visible in the logs.
    """
    excluded: Set[str] = set()
    multipliers: Dict[str, float] = {}
    past = 0
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) != 3 or not all(parts):
            logger.warning(
                f"COIN_EVENT_OVERRIDES: malformed entry '{entry}' ignored "
                f"(expected COIN:YYYY-MM-DD:ACTION)")
            continue
        coin, date_str, action = parts
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(
                f"COIN_EVENT_OVERRIDES: bad date in '{entry}' ignored "
                f"(expected YYYY-MM-DD)")
            continue
        # Validate the action even for non-today dates so typos surface
        # at the next restart rather than on the event day itself.
        multiplier: Optional[float] = None
        if action != "exclude":
            if action.startswith("size"):
                try:
                    multiplier = float(action[len("size"):])
                except ValueError:
                    multiplier = None
            if multiplier is None or not 0.0 < multiplier <= 1.0:
                logger.warning(
                    f"COIN_EVENT_OVERRIDES: bad action in '{entry}' ignored "
                    f"(expected 'exclude' or 'size<m>' with 0 < m <= 1.0)")
                continue
        if event_date < today:
            past += 1
            continue
        if event_date != today:
            continue
        if action == "exclude":
            excluded.add(coin)
            multipliers.pop(coin, None)
        else:
            multipliers[coin] = multiplier
            excluded.discard(coin)
    return excluded, multipliers, past


def _split_event_excluded(
    coins: list, excluded: Set[str]
) -> Tuple[list, list]:
    """Split *coins* into (kept, removed) by the event-exclusion set.

    Module-level on purpose: a comprehension inside the ``Config`` class
    body cannot see class attributes (class scope is skipped inside
    comprehension scopes).
    """
    kept = [c for c in coins if c not in excluded]
    removed = [c for c in coins if c in excluded]
    return kept, removed


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
    # Known: xyz (trade.xyz), flx (Felix), cash (DreamCash), km (Kinetiq), vntl (Ventuals), hyna (HyENA)
    # Leave empty to trade only on standard Hyperliquid.
    # Example: TRADING_DEXES=xyz,flx
    TRADING_DEXES: list = _parse_list(os.getenv("TRADING_DEXES", ""))

    # Whether to also trade standard Hyperliquid perps (non-HIP-3).
    ENABLE_STANDARD_HL: bool = os.getenv("ENABLE_STANDARD_HL", "true").lower() == "true"

    # ------------------------------------------------------------------ #
    # Date-keyed event overrides (earnings days, scheduled macro events)
    # ------------------------------------------------------------------ #
    # Format: COIN_EVENT_OVERRIDES=NVDA:2026-11-17:exclude,NVDA:2026-11-18:size0.5
    # Evaluated ONCE at process start against the current UTC date, so an
    # entry covers the sessions *started* on the listed date. Stateless and
    # idempotent: past dates simply never match again, so there is nothing
    # to revert. Coin names are bare (no "dex:" prefix), matching *_COINS.
    _ev = _parse_event_overrides(
        os.getenv("COIN_EVENT_OVERRIDES", ""),
        datetime.now(timezone.utc).date(),
    )
    EVENT_EXCLUDED_COINS: Set[str] = _ev[0]
    EVENT_SIZE_MULTIPLIERS: Dict[str, float] = _ev[1]
    EVENT_PAST_ENTRIES: int = _ev[2]
    del _ev

    # Per-DEX coin overrides.  If not set, the bot uses coins passed via CLI.
    # Format: comma-separated coin names WITHOUT the "dex:" prefix.
    # Example: XYZ_COINS=XYZ100,XYZ200
    DEX_COINS: dict = {}
    for _dex in TRADING_DEXES:
        _env_key = f"{_dex.upper()}_COINS"
        _val = os.getenv(_env_key, "")
        if _val:
            _coins, _removed = _split_event_excluded(
                _parse_list(_val), EVENT_EXCLUDED_COINS)
            if _removed:
                logger.warning(
                    f"[event-override] {_env_key}: {_removed} excluded for "
                    f"today (UTC); remaining: {_coins or 'NONE'}")
            # Keep the key even when the filter empties the list: a missing
            # key means "discover all coins on the DEX" (_build_hip3_coins
            # checks key presence), which would invert an exclude into
            # trade-everything.
            DEX_COINS[_dex] = _coins

    # ------------------------------------------------------------------ #
    # Builder fee configuration (per-DEX)
    # ------------------------------------------------------------------ #
    # Some HIP-3 deployers require attaching a builder code to every order
    # in order for the wallet to be eligible for their rewards program
    # (e.g. Dreamcash USDT rewards).  Set BUILDER_FEES_<DEX>_ADDRESS to
    # enable per-DEX builder attachment.
    #
    # Two values must stay consistent or every order is rejected:
    #
    #   tenths_bps  per-order builder fee, in tenths of a basis point.
    #               f=10 sends 1 bp (0.01% of notional).  Default 10.
    #   max_fee_rate the pre-approved cap used by approveBuilderFee.
    #               It must be >= the actual per-order fee or the
    #               exchange rejects the order.  Default "0.05%" leaves
    #               headroom for f up to 50 (5 bp) without re-approving.
    #
    # Example for Dreamcash:
    #   BUILDER_FEES_CASH_ADDRESS=0xabc...
    #   BUILDER_FEES_CASH_TENTHS_BPS=10        # 1 bp per order
    #   BUILDER_FEES_CASH_MAX_FEE_RATE=0.05%   # cap at 5 bp
    BUILDER_FEES: dict = {}
    for _dex in TRADING_DEXES:
        _addr = os.getenv(f"BUILDER_FEES_{_dex.upper()}_ADDRESS")
        if _addr:
            BUILDER_FEES[_dex] = {
                "address": _addr.lower(),
                "tenths_bps": int(os.getenv(
                    f"BUILDER_FEES_{_dex.upper()}_TENTHS_BPS", "10"
                )),
                "max_fee_rate": os.getenv(
                    f"BUILDER_FEES_{_dex.upper()}_MAX_FEE_RATE", "0.05%"
                ),
            }

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

    # Force close ALL positions when leverage exceeds this value.
    # Disabled by default – set explicitly to enable.
    FORCE_CLOSE_LEVERAGE: Optional[float] = (
        float(os.getenv("FORCE_CLOSE_LEVERAGE"))
        if os.getenv("FORCE_CLOSE_LEVERAGE") is not None
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

    # How long (seconds) to cache risk metrics before re-fetching from the API.
    METRICS_CACHE_TTL: float = float(os.getenv("METRICS_CACHE_TTL", "2.0"))

    # How long (seconds) to cache asset metadata (sz_decimals etc.).
    META_CACHE_TTL: float = float(os.getenv("META_CACHE_TTL", "3600"))

    # How long (seconds) to cache mid prices in OrderManager.
    MIDS_CACHE_TTL: float = float(os.getenv("MIDS_CACHE_TTL", "5.0"))

    # Timeout (seconds) for Hyperliquid API calls.
    API_TIMEOUT: float = float(os.getenv("API_TIMEOUT", "10"))

    # How often (seconds) to run risk checks in the trading loop.
    # With fast MAIN_LOOP_INTERVAL (e.g. 3s), risk checks don't need to
    # run every cycle. Default 10s matches the previous 10s loop interval.
    RISK_CHECK_INTERVAL: float = max(
        float(os.getenv("RISK_CHECK_INTERVAL", "10")), 1.0
    )

    # ------------------------------------------------------------------ #
    # Margin validation constants
    # ------------------------------------------------------------------ #

    # Minimum order value in USD per coin (JSON dict or single float for default).
    # Example: MIN_ORDER_VALUE_BTC=100, MIN_ORDER_VALUE_ETH=100, MIN_ORDER_VALUE_DEFAULT=50
    MIN_ORDER_VALUE_DEFAULT: float = float(os.getenv("MIN_ORDER_VALUE_DEFAULT", "50"))
    MIN_ORDER_VALUE_BTC: float = float(os.getenv("MIN_ORDER_VALUE_BTC", "100"))
    MIN_ORDER_VALUE_ETH: float = float(os.getenv("MIN_ORDER_VALUE_ETH", "100"))

    # Startup price check.  Coins whose mid price cannot be fetched are retried
    # STARTUP_PRICE_RETRIES times, STARTUP_PRICE_RETRY_INTERVAL seconds apart.
    # With SKIP_UNAVAILABLE_COINS=true a coin that still has no price (and no
    # open position) is dropped for the session instead of aborting startup.
    # Delisted HIP-3 coins are always dropped regardless of this flag.
    SKIP_UNAVAILABLE_COINS: bool = os.getenv("SKIP_UNAVAILABLE_COINS", "false").lower() == "true"
    STARTUP_PRICE_RETRIES: int = max(int(os.getenv("STARTUP_PRICE_RETRIES", "3")), 0)
    STARTUP_PRICE_RETRY_INTERVAL: float = max(float(os.getenv("STARTUP_PRICE_RETRY_INTERVAL", "2.0")), 0.0)

    # Margin multiplier Hyperliquid applies to initial orders.
    INITIAL_MARGIN_MULTIPLIER: float = float(os.getenv("INITIAL_MARGIN_MULTIPLIER", "3.0"))

    # Safety buffer multiplier applied on top of margin calculations.
    MARGIN_SAFETY_BUFFER: float = float(os.getenv("MARGIN_SAFETY_BUFFER", "1.5"))

    @classmethod
    def get_min_order_values(cls) -> Dict[str, float]:
        """Return minimum order values dict, merging per-coin env overrides."""
        values: Dict[str, float] = {'default': cls.MIN_ORDER_VALUE_DEFAULT}
        values['BTC'] = cls.MIN_ORDER_VALUE_BTC
        values['ETH'] = cls.MIN_ORDER_VALUE_ETH
        # Support arbitrary coins via MIN_ORDER_VALUE_<COIN> env vars
        for key, val in os.environ.items():
            if key.startswith("MIN_ORDER_VALUE_") and key not in (
                "MIN_ORDER_VALUE_DEFAULT", "MIN_ORDER_VALUE_BTC", "MIN_ORDER_VALUE_ETH"
            ):
                coin = key[len("MIN_ORDER_VALUE_"):]
                values[coin] = float(val)
        return values

    # Dynamic risk level: green (100%), yellow (50%), red (pause), black (close all).
    # Read at runtime directly from os.getenv("RISK_LEVEL") by RiskManager,
    # so it can be changed without restarting the bot.

    @classmethod
    def validate(cls) -> None:
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
