import logging
import os
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from rate_limiter import api_wrapper

logger = logging.getLogger(__name__)

# Valid risk level values in order of severity.
VALID_RISK_LEVELS = ("green", "yellow", "red", "black")


@dataclass
class RiskMetrics:
    total_balance: float
    available_balance: float
    margin_used: float
    total_position_value: float
    unrealized_pnl: float
    realized_pnl: float
    leverage: float
    margin_ratio: float
    num_positions: int
    timestamp: datetime


# Action priority: higher index = more severe, wins over lower.
_ACTION_PRIORITY = {
    'none': 0,
    'block_new_orders': 1,
    'cooldown': 2,
    'pause': 3,
    'close_all': 4,
    'force_close': 5,
    'stop_bot': 6,
}


class RiskManager:
    def __init__(self, info, account_address: str, config: Dict):
        self.info = info
        self.account_address = account_address
        self.config = config

        # Legacy parameters (backwards compatible)
        self.max_leverage = config.get('max_leverage', 3.0)
        self.max_position_size_pct = config.get('max_position_size_pct', 0.2)
        self.max_drawdown_pct = config.get('max_drawdown_pct', 0.1)
        self.daily_loss_limit_pct = config.get('daily_loss_limit_pct', 0.05)

        # ----- new configurable guardrails ----- #
        self.max_position_pct: float = config.get('max_position_pct', 0.2)
        self.max_margin_usage: float = config.get('max_margin_usage', 0.8)
        self.force_close_margin: Optional[float] = config.get('force_close_margin', None)
        self.daily_loss_limit: Optional[float] = config.get('daily_loss_limit', None)
        self.per_trade_stop_loss: Optional[float] = config.get('per_trade_stop_loss', None)
        self.max_open_positions: int = config.get('max_open_positions', 5)
        self.cooldown_after_stop: int = config.get('cooldown_after_stop', 3600)

        # Tracking state
        self.risk_metrics_history: List[RiskMetrics] = []
        self.starting_balance: Optional[float] = None
        self.daily_starting_balance: Optional[float] = None
        self.last_reset_date = datetime.now().date()

        # Emergency stop cooldown tracking
        self._emergency_stop_time: Optional[datetime] = None

    # ------------------------------------------------------------------ #
    #  Risk level helpers (runtime-reloadable via env var)
    # ------------------------------------------------------------------ #

    def get_risk_level(self) -> str:
        """Read ``RISK_LEVEL`` from the environment so it can change at runtime."""
        level = os.getenv("RISK_LEVEL", "green").lower().strip()
        if level not in VALID_RISK_LEVELS:
            logger.warning(
                "Invalid RISK_LEVEL '%s', falling back to 'green'. "
                "Valid values: %s", level, ", ".join(VALID_RISK_LEVELS)
            )
            return "green"
        return level

    def position_size_multiplier(self) -> float:
        """Return a sizing multiplier based on the current risk level.

        * ``green``  -> 1.0  (full size)
        * ``yellow`` -> 0.5  (half size)
        * ``red``    -> 0.0  (pause – no new positions)
        * ``black``  -> 0.0  (close all – no new positions)
        """
        return {"green": 1.0, "yellow": 0.5, "red": 0.0, "black": 0.0}.get(
            self.get_risk_level(), 1.0
        )

    # ------------------------------------------------------------------ #
    #  Emergency stop cooldown
    # ------------------------------------------------------------------ #

    def record_emergency_stop(self) -> None:
        """Record the timestamp of an emergency stop."""
        self._emergency_stop_time = datetime.now()
        logger.warning(f"Emergency stop recorded at {self._emergency_stop_time}")

    def is_in_cooldown(self) -> bool:
        """Return ``True`` if the bot is still in cooldown after an emergency stop."""
        if self._emergency_stop_time is None:
            return False
        elapsed = (datetime.now() - self._emergency_stop_time).total_seconds()
        return elapsed < self.cooldown_after_stop

    def cooldown_remaining_seconds(self) -> float:
        """Seconds remaining in cooldown (0 if not in cooldown)."""
        if self._emergency_stop_time is None:
            return 0.0
        remaining = self.cooldown_after_stop - (datetime.now() - self._emergency_stop_time).total_seconds()
        return max(0.0, remaining)

    # ------------------------------------------------------------------ #
    #  Metrics collection (with short-lived cache)
    # ------------------------------------------------------------------ #

    def _get_cached_metrics(self, max_age_seconds: float = 2.0) -> Optional[RiskMetrics]:
        """Return the most recent metrics if fresh enough, else fetch new ones."""
        if self.risk_metrics_history:
            last = self.risk_metrics_history[-1]
            age = (datetime.now() - last.timestamp).total_seconds()
            if age < max_age_seconds:
                return last
        return self.get_current_metrics()

    def get_current_metrics(self) -> Optional[RiskMetrics]:
        try:
            user_state = api_wrapper.call(self.info.user_state, self.account_address)

            if not user_state or 'marginSummary' not in user_state:
                return None

            margin_summary = user_state['marginSummary']
            logger.debug(f"Available keys in margin_summary: {list(margin_summary.keys())}")

            account_value = float(margin_summary.get('accountValue', 0))
            total_margin_used = float(margin_summary.get('totalMarginUsed', 0))
            total_position_value = float(margin_summary.get('totalNtlPos', 0))

            # Portfolio Margin: include spot stablecoin balances when perp
            # account is empty so that position sizing works correctly.
            if True:  # Portfolio Margin: always include spot
                try:
                    spot_state = api_wrapper.call(
                        self.info.spot_user_state, self.account_address
                    )
                    for bal in spot_state.get('balances', []):
                        if bal.get('coin', '') in ('USDC', 'USDH', 'USDT0'):
                            account_value += float(bal.get('total', 0))
                    if account_value > 0:
                        logger.debug(f"Using spot balance as collateral: ${account_value:.2f}")
                except Exception as e:
                    logger.debug(f"Could not fetch spot state: {e}")

            available_balance = account_value - total_margin_used
            leverage = total_position_value / account_value if account_value > 0 else 0
            margin_ratio = total_margin_used / account_value if account_value > 0 else 0

            # Compute unrealized PnL from position data
            unrealized_pnl = 0.0
            positions = user_state.get('assetPositions', [])
            for pos in positions:
                pos_data = pos.get('position', pos)
                unrealized_pnl += float(pos_data.get('unrealizedPnl', 0))

            num_positions = len(positions)

            metrics = RiskMetrics(
                total_balance=account_value,
                available_balance=available_balance,
                margin_used=total_margin_used,
                total_position_value=total_position_value,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=0.0,
                leverage=leverage,
                margin_ratio=margin_ratio,
                num_positions=num_positions,
                timestamp=datetime.now()
            )

            if self.starting_balance is None:
                self.starting_balance = metrics.total_balance
                self.daily_starting_balance = metrics.total_balance

            if datetime.now().date() > self.last_reset_date:
                self.daily_starting_balance = metrics.total_balance
                self.last_reset_date = datetime.now().date()

            self.risk_metrics_history.append(metrics)
            if len(self.risk_metrics_history) > 1000:
                self.risk_metrics_history = self.risk_metrics_history[-500:]

            return metrics

        except Exception as e:
            logger.error(f"Error getting risk metrics: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  Core risk check (enhanced with actionable results)
    # ------------------------------------------------------------------ #

    def check_risk_limits(self) -> Dict:
        """Check all risk limits and return an actionable result dict.

        The returned dict is backwards-compatible (contains ``all_checks_passed``
        and ``reason``) but also includes the new ``action``, ``force_close_all``,
        and ``stop_bot`` fields.

        When multiple conditions fire simultaneously, the most severe action
        wins (see ``_ACTION_PRIORITY``).
        """
        metrics = self._get_cached_metrics()
        if not metrics:
            return {
                'all_checks_passed': False,
                'action': 'block_new_orders',
                'force_close_all': False,
                'stop_bot': False,
                'reason': 'No metrics available',
            }

        action = 'none'
        force_close_all = False
        stop_bot = False
        reasons: List[str] = []

        def _escalate(new_action: str) -> None:
            nonlocal action
            if _ACTION_PRIORITY.get(new_action, 0) > _ACTION_PRIORITY.get(action, 0):
                action = new_action

        # --- Risk level checks ------------------------------------------------
        risk_level = self.get_risk_level()
        if risk_level == "black":
            force_close_all = True
            _escalate("close_all")
            reasons.append("RISK_LEVEL is 'black' – closing all positions")
        elif risk_level == "red":
            _escalate("pause")
            reasons.append("RISK_LEVEL is 'red' – pausing trading")

        # --- Cooldown check ---------------------------------------------------
        if self.is_in_cooldown():
            remaining = self.cooldown_remaining_seconds()
            _escalate("cooldown")
            reasons.append(
                f"In cooldown after emergency stop ({remaining:.0f}s remaining)"
            )

        # --- Legacy checks (backwards compatible) -----------------------------
        checks = {
            'leverage_ok': metrics.leverage <= self.max_leverage,
            'margin_ratio_ok': metrics.margin_ratio < self.max_margin_usage,
            'drawdown_ok': self._check_drawdown(metrics),
            'daily_loss_ok': self._check_daily_loss(metrics),
            'max_positions_ok': metrics.num_positions <= self.max_open_positions,
        }

        if not checks['leverage_ok']:
            reasons.append(f"Leverage too high: {metrics.leverage:.2f}")
        if not checks['margin_ratio_ok']:
            reasons.append(f"Margin ratio too high: {metrics.margin_ratio:.2f}")
        if not checks['drawdown_ok']:
            reasons.append("Max drawdown exceeded")
        if not checks['daily_loss_ok']:
            reasons.append("Daily loss limit (%) exceeded")
        if not checks['max_positions_ok']:
            reasons.append(
                f"Max open positions exceeded: {metrics.num_positions}/{self.max_open_positions}"
            )

        if not all(checks.values()):
            _escalate("block_new_orders")

        # --- Force close margin (opt-in) --------------------------------------
        if self.force_close_margin is not None:
            if metrics.margin_ratio >= self.force_close_margin:
                force_close_all = True
                _escalate("force_close")
                reasons.append(
                    f"Margin ratio {metrics.margin_ratio:.2%} >= force_close threshold "
                    f"{self.force_close_margin:.2%}"
                )

        # --- Daily absolute loss limit (opt-in) ------------------------------
        if self.daily_loss_limit is not None and self.daily_starting_balance is not None:
            daily_pnl = metrics.total_balance - self.daily_starting_balance
            if daily_pnl < 0 and abs(daily_pnl) >= self.daily_loss_limit:
                stop_bot = True
                _escalate("stop_bot")
                reasons.append(
                    f"Daily loss ${abs(daily_pnl):.2f} >= limit ${self.daily_loss_limit:.2f}"
                )

        all_passed = action == 'none'

        return {
            'all_checks_passed': all_passed,
            'action': action,
            'force_close_all': force_close_all,
            'stop_bot': stop_bot,
            'reason': "; ".join(reasons) if reasons else "",
            **checks,
        }

    # ------------------------------------------------------------------ #
    #  Per-trade stop loss
    # ------------------------------------------------------------------ #

    def check_per_trade_stop_loss(self, positions: List[Dict]) -> List[Dict]:
        """Return positions that should be closed because they exceed the
        per-trade stop loss threshold.

        Parameters
        ----------
        positions :
            List of position dicts from the Hyperliquid API (``assetPositions``
            → ``position``). Expected keys: ``coin``, ``szi``, ``entryPx``,
            ``unrealizedPnl``, ``positionValue``.

        Returns
        -------
        List of position dicts that should be closed.
        """
        if self.per_trade_stop_loss is None:
            return []

        to_close: List[Dict] = []
        for pos in positions:
            entry_px = float(pos.get('entryPx', 0))
            unrealized_pnl = float(pos.get('unrealizedPnl', 0))
            # positionValue may not always be present; fall back to szi * entryPx
            position_value = float(pos.get('positionValue', 0))
            if position_value <= 0:
                szi = float(pos.get('szi', 0))
                position_value = abs(szi) * entry_px

            if position_value <= 0 or entry_px <= 0:
                continue

            loss_pct = abs(unrealized_pnl) / position_value if unrealized_pnl < 0 else 0.0

            if loss_pct >= self.per_trade_stop_loss:
                coin = pos.get('coin', 'UNKNOWN')
                logger.warning(
                    "Per-trade stop loss triggered for %s: loss %.2f%% >= threshold %.2f%%",
                    coin, loss_pct * 100, self.per_trade_stop_loss * 100,
                )
                to_close.append(pos)

        return to_close

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _check_drawdown(self, metrics: RiskMetrics) -> bool:
        if not self.starting_balance:
            return True
        drawdown = (self.starting_balance - metrics.total_balance) / self.starting_balance
        return drawdown <= self.max_drawdown_pct

    def _check_daily_loss(self, metrics: RiskMetrics) -> bool:
        if not self.daily_starting_balance:
            return True
        daily_loss = (self.daily_starting_balance - metrics.total_balance) / self.daily_starting_balance
        return daily_loss <= self.daily_loss_limit_pct

    # ------------------------------------------------------------------ #
    #  Position sizing
    # ------------------------------------------------------------------ #

    def calculate_position_size_limit(self, coin: str, current_price: float) -> float:
        metrics = self._get_cached_metrics()
        if not metrics:
            return 0

        max_position_value = metrics.total_balance * self.max_position_pct
        available_margin = metrics.available_balance
        max_position_with_leverage = available_margin * self.max_leverage

        max_allowed_value = min(max_position_value, max_position_with_leverage)

        # Apply risk-level multiplier
        max_allowed_value *= self.position_size_multiplier()

        max_size = max_allowed_value / current_price if current_price > 0 else 0
        return max_size

    def should_allow_new_position(self, coin: str, size: float, price: float) -> bool:
        risk_checks = self.check_risk_limits()
        if not risk_checks['all_checks_passed']:
            logger.warning(f"Risk check failed: {risk_checks.get('reason')}")
            return False

        max_size = self.calculate_position_size_limit(coin, price)
        if size > max_size:
            logger.warning(f"Position size {size} exceeds limit {max_size}")
            return False

        return True

    # ------------------------------------------------------------------ #
    #  Summary
    # ------------------------------------------------------------------ #

    def get_risk_summary(self) -> Dict:
        metrics = self._get_cached_metrics()
        if not metrics:
            return {'status': 'No data available'}

        risk_checks = self.check_risk_limits()

        summary = {
            'current_balance': metrics.total_balance,
            'available_balance': metrics.available_balance,
            'leverage': metrics.leverage,
            'margin_ratio': metrics.margin_ratio,
            'unrealized_pnl': metrics.unrealized_pnl,
            'num_positions': metrics.num_positions,
            'risk_level': self.get_risk_level(),
            'size_multiplier': self.position_size_multiplier(),
            'in_cooldown': self.is_in_cooldown(),
            'risk_status': 'OK' if risk_checks['all_checks_passed'] else 'WARNING',
            'risk_action': risk_checks.get('action', 'none'),
            'risk_checks': risk_checks,
        }

        if self.starting_balance:
            total_pnl_pct = ((metrics.total_balance - self.starting_balance) / self.starting_balance) * 100
            summary['total_pnl_pct'] = total_pnl_pct

        if self.daily_starting_balance:
            daily_pnl_pct = ((metrics.total_balance - self.daily_starting_balance) / self.daily_starting_balance) * 100
            summary['daily_pnl_pct'] = daily_pnl_pct

        return summary
