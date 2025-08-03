import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from rate_limiter import api_wrapper

logger = logging.getLogger(__name__)


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


class RiskManager:
    def __init__(self, info, account_address: str, config: Dict):
        self.info = info
        self.account_address = account_address
        self.config = config
        self.max_leverage = config.get('max_leverage', 3.0)
        self.max_position_size_pct = config.get('max_position_size_pct', 0.2)
        self.max_drawdown_pct = config.get('max_drawdown_pct', 0.1)
        self.daily_loss_limit_pct = config.get('daily_loss_limit_pct', 0.05)
        self.risk_metrics_history = []
        self.starting_balance = None
        self.daily_starting_balance = None
        self.last_reset_date = datetime.now().date()
        
    def get_current_metrics(self) -> Optional[RiskMetrics]:
        try:
            user_state = api_wrapper.call(self.info.user_state, self.account_address)
            
            if not user_state or 'marginSummary' not in user_state:
                return None
                
            margin_summary = user_state['marginSummary']
            
            # Debug: Print available keys
            logger.debug(f"Available keys in margin_summary: {list(margin_summary.keys())}")
            
            # Use .get() with defaults to handle missing keys
            # Based on available keys: ['accountValue', 'totalNtlPos', 'totalRawUsd', 'totalMarginUsed']
            account_value = float(margin_summary.get('accountValue', 0))
            total_margin_used = float(margin_summary.get('totalMarginUsed', 0))
            total_position_value = float(margin_summary.get('totalNtlPos', 0))
            
            # Calculate derived values
            available_balance = account_value - total_margin_used
            leverage = total_position_value / account_value if account_value > 0 else 0
            margin_ratio = total_margin_used / account_value if account_value > 0 else 0
            
            metrics = RiskMetrics(
                total_balance=account_value,
                available_balance=available_balance,
                margin_used=total_margin_used,
                total_position_value=total_position_value,
                unrealized_pnl=0.0,  # Not available in current response
                realized_pnl=0.0,  # Not available in current response
                leverage=leverage,
                margin_ratio=margin_ratio,
                num_positions=len(user_state.get('assetPositions', [])),
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
    
    def check_risk_limits(self) -> Dict[str, bool]:
        metrics = self.get_current_metrics()
        if not metrics:
            return {'all_checks_passed': False, 'reason': 'No metrics available'}
            
        checks = {
            'leverage_ok': metrics.leverage <= self.max_leverage,
            'margin_ratio_ok': metrics.margin_ratio < 0.8,
            'drawdown_ok': self._check_drawdown(metrics),
            'daily_loss_ok': self._check_daily_loss(metrics),
            'all_checks_passed': True
        }
        
        checks['all_checks_passed'] = all([
            checks['leverage_ok'],
            checks['margin_ratio_ok'],
            checks['drawdown_ok'],
            checks['daily_loss_ok']
        ])
        
        if not checks['all_checks_passed']:
            reasons = []
            if not checks['leverage_ok']:
                reasons.append(f"Leverage too high: {metrics.leverage:.2f}")
            if not checks['margin_ratio_ok']:
                reasons.append(f"Margin ratio too high: {metrics.margin_ratio:.2f}")
            if not checks['drawdown_ok']:
                reasons.append("Max drawdown exceeded")
            if not checks['daily_loss_ok']:
                reasons.append("Daily loss limit exceeded")
            checks['reason'] = "; ".join(reasons)
            
        return checks
    
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
    
    def calculate_position_size_limit(self, coin: str, current_price: float) -> float:
        metrics = self.get_current_metrics()
        if not metrics:
            return 0
            
        max_position_value = metrics.total_balance * self.max_position_size_pct
        
        available_margin = metrics.available_balance
        max_position_with_leverage = available_margin * self.max_leverage
        
        max_allowed_value = min(max_position_value, max_position_with_leverage)
        
        max_size = max_allowed_value / current_price
        
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
    
    def get_risk_summary(self) -> Dict:
        metrics = self.get_current_metrics()
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
            'risk_status': 'OK' if risk_checks['all_checks_passed'] else 'WARNING',
            'risk_checks': risk_checks
        }
        
        if self.starting_balance:
            total_pnl_pct = ((metrics.total_balance - self.starting_balance) / self.starting_balance) * 100
            summary['total_pnl_pct'] = total_pnl_pct
            
        if self.daily_starting_balance:
            daily_pnl_pct = ((metrics.total_balance - self.daily_starting_balance) / self.daily_starting_balance) * 100
            summary['daily_pnl_pct'] = daily_pnl_pct
            
        return summary