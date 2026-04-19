"""WebSocket auto-reconnect monitor.

Periodically checks whether the WebSocket feed is alive by inspecting
:meth:`MarketDataFeed.stale_coins`.  When **all** coins are stale for
longer than ``stale_threshold`` seconds the entire WS stack is torn
down and rebuilt.

The reconnector uses exponential back-off: 5 → 10 → 20 → … → 300 s
between attempts, resetting after a successful reconnection.

Usage (called from the bot main loop)::

    reconnector = WsReconnector(stale_threshold=60.0)
    # inside loop:
    reconnector.maybe_reconnect(bot)
"""

import logging
import time

logger = logging.getLogger(__name__)

_MIN_BACKOFF = 5.0
_MAX_BACKOFF = 300.0


class WsReconnector:
    """Monitors WS health and rebuilds the feed stack when dead."""

    def __init__(self, stale_threshold: float = 60.0) -> None:
        self.stale_threshold = stale_threshold
        self._last_check: float = 0.0
        self._check_interval: float = 30.0  # seconds between health checks
        self._consecutive_failures: int = 0
        self._next_retry_at: float = 0.0
        self._reconnect_count: int = 0

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def maybe_reconnect(self, bot: "HyperliquidBot") -> None:  # noqa: F821
        """Check WS health and reconnect if needed.  Safe to call every cycle."""
        now = time.monotonic()
        if now - self._last_check < self._check_interval:
            return
        self._last_check = now

        if self._is_healthy(bot):
            if self._consecutive_failures > 0:
                logger.info("[ws-reconnect] WS recovered (was failing)")
                self._consecutive_failures = 0
                self._next_retry_at = 0.0
            return

        # WS is stale — attempt reconnect (respecting back-off)
        if now < self._next_retry_at:
            return

        self._do_reconnect(bot)

    @property
    def stats(self) -> dict:
        return {
            "reconnect_count": self._reconnect_count,
            "consecutive_failures": self._consecutive_failures,
        }

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _is_healthy(self, bot: "HyperliquidBot") -> bool:  # noqa: F821
        """Return True if at least one coin received a WS update recently."""
        if bot.ws_feed is None:
            return False

        stale = bot.ws_feed.stale_coins(max_age=self.stale_threshold)
        total = len(bot.ws_feed.coins)
        if len(stale) < total:
            return True

        # All coins stale
        return False

    def _do_reconnect(self, bot: "HyperliquidBot") -> None:  # noqa: F821
        """Tear down and rebuild the entire WS stack."""
        self._consecutive_failures += 1
        backoff = min(_MIN_BACKOFF * (2 ** (self._consecutive_failures - 1)), _MAX_BACKOFF)
        self._next_retry_at = time.monotonic() + backoff

        logger.warning(
            "[ws-reconnect] WS stale for >%.0fs — reconnecting (attempt #%d, next retry in %.0fs)",
            self.stale_threshold,
            self._consecutive_failures,
            backoff,
        )

        try:
            self._teardown(bot)
            self._rebuild(bot)
            self._reconnect_count += 1
            self._consecutive_failures = 0
            self._next_retry_at = 0.0
            logger.info("[ws-reconnect] Reconnected successfully (total reconnects: %d)", self._reconnect_count)
        except Exception as e:
            logger.error("[ws-reconnect] Reconnect failed: %s", e)

    def _teardown(self, bot: "HyperliquidBot") -> None:  # noqa: F821
        """Stop all WS feeds and guards, then close the WS manager."""
        if bot.imbalance_guard:
            bot.imbalance_guard.stop()
            bot.imbalance_guard = None
        if bot.bbo_guard:
            bot.bbo_guard.stop()
            bot.bbo_guard = None
        if bot.fill_feed:
            bot.fill_feed.stop()
            bot.fill_feed = None
        if bot.ws_feed:
            # Stop the underlying SDK WebSocket manager thread
            ws_mgr = getattr(bot.ws_feed.info, 'ws_manager', None)
            bot.ws_feed.stop()
            if ws_mgr is not None:
                try:
                    ws_mgr.stop()
                except Exception:
                    pass
            bot.ws_feed = None

    def _rebuild(self, bot: "HyperliquidBot") -> None:  # noqa: F821
        """Create fresh WS Info + feeds + guards."""
        from hyperliquid.info import Info as WsInfo
        from ws import MarketDataFeed, FillFeed, BboGuard, ImbalanceGuard
        from config import Config

        perp_dexs = bot._build_perp_dexs()
        ws_info = WsInfo(
            base_url=Config.API_URL,
            skip_ws=False,
            perp_dexs=perp_dexs,
            timeout=bot.api_timeout,
        )

        bot.ws_feed = MarketDataFeed(ws_info, bot.market_data, bot.coins)
        bot.ws_feed.start()

        tracker = getattr(bot.strategy, 'order_tracker', None)
        if tracker is not None:
            bot.fill_feed = FillFeed(ws_info, tracker, bot.account_address)
            bot.fill_feed.start()

            threshold = bot.strategy_config.get('bbo_guard_threshold_bps', 2.0)
            if threshold > 0:
                bot.bbo_guard = BboGuard(tracker, threshold_bps=threshold)
                bot.ws_feed.add_listener(bot.bbo_guard.on_l2_update)
                logger.info("[ws-reconnect] BboGuard re-enabled (threshold=%.1f bps)", threshold)

            imb_threshold = bot.strategy_config.get('imbalance_guard_threshold', 0)
            if imb_threshold > 0:
                bot.imbalance_guard = ImbalanceGuard(
                    tracker,
                    threshold=imb_threshold,
                    depth=int(bot.strategy_config.get('imbalance_guard_depth', 5)),
                )
                bot.ws_feed.add_listener(bot.imbalance_guard.on_l2_update)
                logger.info(
                    "[ws-reconnect] ImbalanceGuard re-enabled (threshold=%.2f, depth=%d)",
                    imb_threshold, bot.imbalance_guard.depth,
                )
