"""Oracle divergence / momentum guard for adverse selection prevention.

HIP-3 deployers push ``oraclePx`` roughly every 3 seconds, with each
update capped at 1% of the previous value.  That stream is a free,
low-latency copy of the external market that informed traders trade
against — while the bot's resting quotes float on the local book.  This
guard consumes ``activeAssetCtx`` updates (via
:meth:`MarketDataFeed.add_ctx_listener`) plus ``l2Book`` mids and fires
two defensive signals:

- **Divergence gate**: when ``|book mid − oraclePx|`` exceeds a
  threshold, the stale side's entry quotes are cancelled (oracle above
  mid → resting SELLs are stale-cheap → cancel ``"A"``; oracle below
  mid → cancel ``"B"``).
- **Momentum gate**: when a single oracle step pins near the 1% cap, or
  ``momentum_consecutive`` same-direction steps of at least
  ``momentum_min_step_bps`` occur, both sides are cancelled and new
  placement is blocked for ``block_seconds``.

A staleness TTL doubles as an automatic market-close gate: equity-perp
oracles stop updating outside US market hours, and a floating book is
then a normal state, not a divergence — the guard fully disarms until
fresh oracle updates resume (re-baselining on recovery so the first
post-open step does not fire a spurious signal).

Follows the WS-guard conventions of :class:`ImbalanceGuard`:
state-transition firing, ``min_cancel_interval`` rate limiting, 5-minute
summaries, ``stats`` property and ``stop()`` lifecycle.  Fail-safe by
design: missing oracle data, missing mids or parse failures leave the
guard inert (identical to pre-guard behaviour).

Usage::

    guard = OracleDivergenceGuard(order_tracker, divergence_threshold_bps=5.0)
    market_data_feed.add_listener(guard.on_l2_update)
    market_data_feed.add_ctx_listener(guard.on_asset_ctx_update)
    ...
    guard.stop()
"""

import logging
import threading
import time
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Divergence state constants
_NEUTRAL = "neutral"
_SELL_STALE = "sell_stale"   # oracle > mid → resting SELLs are stale-cheap
_BUY_STALE = "buy_stale"     # oracle < mid → resting BUYs are stale-rich


class OracleDivergenceGuard:
    """Cancel stale-side quotes on oracle divergence / momentum."""

    def __init__(
        self,
        order_tracker: Any,
        *,
        divergence_threshold_bps: float = 5.0,
        momentum_cap_pin_bps: float = 80.0,
        momentum_min_step_bps: float = 5.0,
        momentum_consecutive: int = 3,
        stale_ttl_seconds: float = 30.0,
        block_seconds: float = 10.0,
        min_cancel_interval: float = 2.0,
    ) -> None:
        self.order_tracker = order_tracker
        self.divergence_threshold_bps = divergence_threshold_bps
        self.momentum_cap_pin_bps = momentum_cap_pin_bps
        self.momentum_min_step_bps = momentum_min_step_bps
        self.momentum_consecutive = momentum_consecutive
        self.stale_ttl_seconds = stale_ttl_seconds
        self.block_seconds = block_seconds
        self.min_cancel_interval = min_cancel_interval

        # Per-coin state — written on the WS thread, read from the main
        # loop (get_blocked_sides), so every access goes through the lock.
        self._last_oracle_px: Dict[str, float] = {}
        # Monotonic time the oracle *value* last changed (not last message)
        self._last_oracle_change: Dict[str, float] = {}
        self._last_mid: Dict[str, float] = {}
        self._momentum_dir: Dict[str, int] = {}
        self._momentum_count: Dict[str, int] = {}
        self._div_state: Dict[str, str] = {}
        self._blocked_until: Dict[str, float] = {}
        self._side_blocked_until: Dict[Tuple[str, str], float] = {}
        self._last_cancel_time: Dict[str, float] = {}
        self._lock = threading.Lock()

        # Counters
        self._ctx_update_count = 0
        self._divergence_fires = 0
        self._momentum_fires = 0
        self._cancels_triggered = 0
        self._skipped_rate_limit = 0
        self._error_count = 0
        self._running = True

        # Per-coin max absolute divergence seen (bps, for threshold tuning)
        self._max_divergence: Dict[str, float] = {}
        self._summary_interval = 300.0
        self._last_summary_time = time.monotonic()

    # ------------------------------------------------------------------ #
    #  Callbacks (WS thread)
    # ------------------------------------------------------------------ #

    def on_asset_ctx_update(self, coin: str, ctx: Any) -> None:
        """Callback from MarketDataFeed ctx listener.  Runs on the WS thread."""
        if not self._running:
            return
        try:
            oracle = self._parse_oracle_px(ctx)
            if oracle is None:
                return

            now = time.monotonic()
            momentum_fire = False
            with self._lock:
                prev = self._last_oracle_px.get(coin)
                last_change = self._last_oracle_change.get(coin, 0.0)

                if prev is None or (now - last_change) > self.stale_ttl_seconds:
                    # First sighting or stale recovery: re-baseline the
                    # value but do NOT arm the guard on a mere snapshot.
                    # A frozen, market-closed oracle must not be treated as
                    # fresh just because activeAssetCtx delivered a value on
                    # subscribe — we cannot yet tell a live oracle from a
                    # frozen one. Stamp freshness only once we observe the
                    # value actually change while ticking; the guard then
                    # arms one update later and never fires on an off-hours
                    # floating book at startup / reconnect.
                    self._momentum_dir.pop(coin, None)
                    self._momentum_count.pop(coin, None)
                    if prev is not None and oracle != prev:
                        self._last_oracle_change[coin] = now
                    self._last_oracle_px[coin] = oracle
                    return

                if oracle == prev:
                    # Same-value push: no step, and freshness is NOT
                    # renewed so a flat oracle correctly decays to stale.
                    return

                self._last_oracle_change[coin] = now
                step_bps = (oracle - prev) / prev * 1e4
                self._last_oracle_px[coin] = oracle
                self._ctx_update_count += 1

                if self.momentum_cap_pin_bps > 0 and abs(step_bps) >= self.momentum_cap_pin_bps:
                    # Cap pinning: the real market is beyond the capped
                    # oracle — the next update is almost surely same-way.
                    momentum_fire = True
                elif self.momentum_min_step_bps > 0 and abs(step_bps) >= self.momentum_min_step_bps:
                    direction = 1 if step_bps > 0 else -1
                    if direction == self._momentum_dir.get(coin, 0):
                        self._momentum_count[coin] = self._momentum_count.get(coin, 0) + 1
                    else:
                        self._momentum_dir[coin] = direction
                        self._momentum_count[coin] = 1
                    if (self.momentum_consecutive > 0
                            and self._momentum_count[coin] >= self.momentum_consecutive):
                        momentum_fire = True
                else:
                    self._momentum_dir.pop(coin, None)
                    self._momentum_count.pop(coin, None)

                if momentum_fire:
                    self._momentum_fires += 1
                    self._blocked_until[coin] = now + self.block_seconds
                    self._momentum_dir.pop(coin, None)
                    self._momentum_count.pop(coin, None)

            if momentum_fire:
                logger.info(
                    "[oracle-guard] Momentum fired for %s (step=%.1fbps) — cancelling both sides",
                    coin, step_bps,
                )
                self._try_cancel_both(coin)

            # Divergence is re-evaluated on the oracle side too, using the
            # latest cached mid.
            self._evaluate_divergence(coin, now)

        except Exception as e:
            self._error_count += 1
            if self._error_count <= 5 or self._error_count % 100 == 0:
                logger.error("[oracle-guard] ctx error: %s", e)

    def on_l2_update(self, coin: str, levels: Any) -> None:
        """Callback from MarketDataFeed l2Book listener.  Runs on the WS thread."""
        if not self._running:
            return
        try:
            mid = self._extract_mid(levels)
            if mid is None:
                return
            with self._lock:
                self._last_mid[coin] = mid
            self._evaluate_divergence(coin, time.monotonic())
        except Exception as e:
            self._error_count += 1
            if self._error_count <= 5 or self._error_count % 100 == 0:
                logger.error("[oracle-guard] l2 error: %s", e)

    # ------------------------------------------------------------------ #
    #  Divergence gate
    # ------------------------------------------------------------------ #

    def _evaluate_divergence(self, coin: str, now: float) -> None:
        fire_side: Optional[str] = None
        div_bps = 0.0
        with self._lock:
            oracle = self._last_oracle_px.get(coin)
            last_change = self._last_oracle_change.get(coin, 0.0)
            if oracle is None or (now - last_change) > self.stale_ttl_seconds:
                # Market-close gate: a floating book against a frozen
                # oracle is a normal state, not a divergence.
                self._div_state[coin] = _NEUTRAL
                return
            mid = self._last_mid.get(coin)
            if not mid or mid <= 0:
                return

            div_bps = (oracle - mid) / mid * 1e4
            if abs(div_bps) > self._max_divergence.get(coin, 0.0):
                self._max_divergence[coin] = abs(div_bps)

            if self.divergence_threshold_bps <= 0:
                return  # divergence gate disabled
            if div_bps > self.divergence_threshold_bps:
                new_state = _SELL_STALE
            elif div_bps < -self.divergence_threshold_bps:
                new_state = _BUY_STALE
            else:
                new_state = _NEUTRAL

            prev_state = self._div_state.get(coin, _NEUTRAL)
            self._div_state[coin] = new_state

            # State-transition model: fire only on entry into a risky state
            if new_state != prev_state and new_state != _NEUTRAL:
                self._divergence_fires += 1
                fire_side = "A" if new_state == _SELL_STALE else "B"
                self._side_blocked_until[(coin, fire_side)] = now + self.block_seconds

        if fire_side is not None:
            logger.info(
                "[oracle-guard] Divergence %.1fbps for %s — cancelling %s side",
                div_bps, coin, "SELL" if fire_side == "A" else "BUY",
            )
            self._try_cancel(coin, fire_side)

    # ------------------------------------------------------------------ #
    #  Placement skip hook (main loop thread)
    # ------------------------------------------------------------------ #

    def get_blocked_sides(self, coin: str) -> Set[str]:
        """Return the sides currently blocked for placement (``"B"``/``"A"``).

        Called from the strategy's placement path on the main loop.
        Expired entries are lazily removed.
        """
        now = time.monotonic()
        blocked: Set[str] = set()
        with self._lock:
            if self._blocked_until.get(coin, 0.0) > now:
                blocked.update(("B", "A"))
            elif coin in self._blocked_until:
                del self._blocked_until[coin]
            for side in ("B", "A"):
                key = (coin, side)
                if self._side_blocked_until.get(key, 0.0) > now:
                    blocked.add(side)
                elif key in self._side_blocked_until:
                    del self._side_blocked_until[key]
        return blocked

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_oracle_px(ctx: Any) -> Optional[float]:
        """Extract oraclePx from an activeAssetCtx payload.  None = unusable."""
        if not isinstance(ctx, dict):
            return None
        raw = ctx.get("oraclePx")
        if raw is None:
            return None
        try:
            oracle = float(raw)
        except (TypeError, ValueError):
            return None
        return oracle if oracle > 0 else None

    @staticmethod
    def _extract_mid(levels: Any) -> Optional[float]:
        """Extract book mid from raw l2Book levels (same shape as BboGuard)."""
        if not levels or len(levels) < 2:
            return None
        bids = levels[0]
        asks = levels[1]
        if not bids or not asks:
            return None
        bid = float(bids[0]["px"])
        ask = float(asks[0]["px"])
        if bid <= 0 or ask <= 0:
            return None
        return (bid + ask) / 2

    def _rate_limited(self, key: str, now: float) -> bool:
        """Return True (and count) if *key* fired within min_cancel_interval."""
        with self._lock:
            last = self._last_cancel_time.get(key, 0)
            if now - last < self.min_cancel_interval:
                self._skipped_rate_limit += 1
                return True
            self._last_cancel_time[key] = now
        return False

    def _try_cancel(self, coin: str, side: str) -> None:
        """Cancel entry orders on *side* if not rate-limited (divergence gate)."""
        if self._rate_limited(f"{coin}:{side}", time.monotonic()):
            return
        self.order_tracker.cancel_orders_by_side(coin, side)
        self._cancels_triggered += 1

    def _try_cancel_both(self, coin: str) -> None:
        """Cancel both entry sides in a single API call (momentum gate).

        One ``cancel_all_orders_for_coin`` round-trip instead of two
        sequential per-side calls keeps WS-thread blocking (and API weight)
        low during exactly the fast-market moments the momentum gate
        targets. Close orders are managed elsewhere and untouched.
        """
        if self._rate_limited(f"{coin}:both", time.monotonic()):
            return
        self.order_tracker.cancel_all_orders_for_coin(coin, reason="oracle_momentum")
        self._cancels_triggered += 1

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def maybe_log_summary(self) -> None:
        """Log periodic summary if interval has elapsed (main loop)."""
        now = time.monotonic()
        if now - self._last_summary_time < self._summary_interval:
            return
        self._last_summary_time = now
        self._log_summary()

    def _log_summary(self) -> None:
        with self._lock:
            max_div = dict(self._max_divergence)
            self._max_divergence.clear()
            updates = self._ctx_update_count
            self._ctx_update_count = 0
            stale = [
                coin for coin in self._last_oracle_px
                if time.monotonic() - self._last_oracle_change.get(coin, 0.0)
                > self.stale_ttl_seconds
            ]

        if not max_div and updates == 0:
            return

        coin_parts = ", ".join(
            f"{coin}={div:.1f}" for coin, div in sorted(max_div.items(), key=lambda x: -x[1])
        )
        logger.info(
            f"[oracle-guard] Summary: oracle_updates={updates} "
            f"div_fires={self._divergence_fires} mom_fires={self._momentum_fires} "
            f"cancels={self._cancels_triggered} skipped={self._skipped_rate_limit} "
            f"stale_coins={sorted(stale)} max_divergence_bps=[{coin_parts}]"
        )

    def stop(self) -> None:
        """Stop the guard and log a final summary."""
        self._running = False
        self._log_summary()
        logger.info(
            "[oracle-guard] Stopped (div_fires=%d, mom_fires=%d, cancels=%d, errors=%d)",
            self._divergence_fires,
            self._momentum_fires,
            self._cancels_triggered,
            self._error_count,
        )

    # ------------------------------------------------------------------ #
    #  Observability
    # ------------------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> Dict:
        with self._lock:
            return {
                "running": self._running,
                "oracle_updates": self._ctx_update_count,
                "divergence_fires": self._divergence_fires,
                "momentum_fires": self._momentum_fires,
                "cancels_triggered": self._cancels_triggered,
                "skipped_rate_limit": self._skipped_rate_limit,
                "errors": self._error_count,
                "max_divergence": dict(self._max_divergence),
            }
