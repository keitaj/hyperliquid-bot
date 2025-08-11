"""
Margin and configuration validator for Hyperliquid trading bot
Validates margin requirements based on strategy and parameters
"""

import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of validation check"""
    is_valid: bool
    message: str
    recommendations: List[str] = None



class MarginValidator:
    """Validates margin requirements and trading configuration"""

    # Hyperliquid minimum order values
    # IMPORTANT: Hyperliquid appears to require higher minimums for initial orders
    MIN_ORDER_VALUES = {
        'BTC': 100.0,   # Actual minimum appears to be $100 for BTC
        'ETH': 100.0,   # Actual minimum appears to be $100 for ETH
        'default': 50.0
    }

    # Leverage and margin requirements
    DEFAULT_LEVERAGE = 10.0
    MARGIN_REQUIREMENT = 0.1  # 10% for 10x leverage
    INITIAL_MARGIN_MULTIPLIER = 3.0  # Hyperliquid requires 3x margin for initial orders
    SAFETY_BUFFER = 1.5  # 50% safety buffer

    # Strategy-specific multipliers for risk assessment
    STRATEGY_RISK_MULTIPLIERS = {
        'grid_trading': 2.0,  # Grid uses multiple orders
        'breakout': 1.5,      # Breakout can be volatile
        'simple_ma': 1.0,     # Standard risk
        'rsi': 1.0,           # Standard risk
        'bollinger_bands': 1.2,  # Slightly higher due to volatility
        'macd': 1.0,          # Standard risk
    }

    def __init__(self, info, account_address: str):
        self.info = info
        self.account_address = account_address

    def get_account_info(self) -> Tuple[float, float]:
        """Get account value and available balance"""
        try:
            user_state = self.info.user_state(self.account_address)

            if 'marginSummary' not in user_state:
                logger.error("Could not retrieve margin information")
                return 0.0, 0.0

            margin_summary = user_state['marginSummary']
            account_value = float(margin_summary.get('accountValue', 0))
            margin_used = float(margin_summary.get('totalMarginUsed', 0))
            available_balance = account_value - margin_used

            return account_value, available_balance

        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return 0.0, 0.0

    def validate_strategy_config(
        self,
        strategy_name: str,
        strategy_config: Dict,
        coins: List[str],
        current_prices: Dict[str, float]
    ) -> ValidationResult:
        """Validate if strategy configuration is viable with current account"""

        account_value, available_balance = self.get_account_info()

        if account_value <= 0:
            return ValidationResult(
                is_valid=False,
                message="Could not retrieve account information",
                recommendations=["Check API connection and credentials"]
            )

        # Extract strategy parameters
        if strategy_name == 'grid_trading':
            # Grid trading has different parameters
            grid_levels = strategy_config.get('grid_levels', 10)
            position_size = strategy_config.get('position_size_per_grid', 50)
            max_positions = min(grid_levels, strategy_config.get('max_positions', 5))
        else:
            position_size = strategy_config.get('position_size_usd', 100)
            max_positions = strategy_config.get('max_positions', 3)

        # Get strategy risk multiplier
        risk_multiplier = self.STRATEGY_RISK_MULTIPLIERS.get(strategy_name, 1.0)

        # Calculate total exposure and margin requirements
        total_exposure = position_size * max_positions

        # For initial orders, Hyperliquid requires more margin
        # This is the actual issue causing "Insufficient margin" errors
        base_margin_required = total_exposure * self.MARGIN_REQUIREMENT * self.INITIAL_MARGIN_MULTIPLIER
        margin_required_with_buffer = base_margin_required * self.SAFETY_BUFFER * risk_multiplier

        # Check minimum order values for each coin
        min_order_issues = []
        for coin in coins:
            min_value = self.MIN_ORDER_VALUES.get(coin, self.MIN_ORDER_VALUES['default'])
            if position_size < min_value:
                min_order_issues.append(f"{coin}: ${position_size:.2f} < ${min_value:.2f} minimum")

        # Calculate position sizes in coin units
        position_sizes_in_units = {}
        for coin in coins:
            if coin in current_prices and current_prices[coin] > 0:
                size_in_units = position_size / current_prices[coin]
                position_sizes_in_units[coin] = size_in_units

        # Generate validation report
        logger.info("=" * 60)
        logger.info("MARGIN VALIDATION REPORT")
        logger.info("=" * 60)
        logger.info(f"Strategy: {strategy_name}")
        logger.info(f"Coins: {', '.join(coins)}")
        logger.info("-" * 60)
        logger.info("ACCOUNT STATUS:")
        logger.info(f"  Account Value: ${account_value:.2f}")
        logger.info(f"  Available Balance: ${available_balance:.2f}")
        logger.info("-" * 60)
        logger.info("STRATEGY CONFIGURATION:")
        logger.info(f"  Position Size: ${position_size:.2f}")
        logger.info(f"  Max Positions: {max_positions}")
        logger.info(f"  Total Exposure: ${total_exposure:.2f}")
        logger.info(f"  Risk Multiplier: {risk_multiplier}x ({strategy_name})")
        logger.info("-" * 60)
        logger.info("MARGIN REQUIREMENTS:")
        logger.info(f"  Base Margin (10%): ${base_margin_required:.2f}")
        logger.info(f"  With Safety Buffer: ${margin_required_with_buffer:.2f}")
        logger.info(f"  Account Coverage: {(account_value/margin_required_with_buffer)*100:.1f}%")

        # Position sizes for each coin
        if position_sizes_in_units:
            logger.info("-" * 60)
            logger.info("POSITION SIZES:")
            for coin, size in position_sizes_in_units.items():
                logger.info(f"  {coin}: {size:.6f} units (${position_size:.2f})")

        # Validation results
        validation_passed = True
        recommendations = []

        # Check margin sufficiency
        if margin_required_with_buffer > account_value:
            validation_passed = False
            recommended_position_size = (account_value / (risk_multiplier * self.SAFETY_BUFFER * self.MARGIN_REQUIREMENT * max_positions))
            recommendations.append(f"Reduce position_size_usd to ${recommended_position_size:.2f} or less")
            recommendations.append(f"Or reduce max_positions to {int(account_value / (position_size * self.MARGIN_REQUIREMENT * self.SAFETY_BUFFER * risk_multiplier))} or less")
            recommendations.append(f"Or add at least ${margin_required_with_buffer - account_value:.2f} to your account")

        # Check minimum order values
        if min_order_issues:
            validation_passed = False
            recommendations.append(f"Increase position_size_usd to at least ${max(self.MIN_ORDER_VALUES.values()):.2f}")
            for issue in min_order_issues:
                logger.warning(f"  Minimum order value issue: {issue}")

        # Special checks for grid trading
        if strategy_name == 'grid_trading':
            grid_margin = position_size * grid_levels * self.MARGIN_REQUIREMENT
            if grid_margin > account_value:
                validation_passed = False
                max_grids = int(account_value / (position_size * self.MARGIN_REQUIREMENT))
                recommendations.append(f"Reduce grid_levels to {max_grids} or less")
                recommendations.append(f"Or reduce position_size_per_grid to ${account_value / (grid_levels * self.MARGIN_REQUIREMENT):.2f}")

        logger.info("-" * 60)
        if validation_passed:
            logger.info("✅ VALIDATION PASSED")
            logger.info(f"Margin utilization: {(margin_required_with_buffer/account_value)*100:.1f}%")
            logger.info(f"Free margin after max positions: ${account_value - margin_required_with_buffer:.2f}")
            message = "Configuration is valid for trading"
        else:
            logger.error("❌ VALIDATION FAILED")
            message = "Insufficient margin or invalid configuration"
            logger.error("RECOMMENDATIONS:")
            for i, rec in enumerate(recommendations, 1):
                logger.error(f"  {i}. {rec}")

        logger.info("=" * 60)

        return ValidationResult(
            is_valid=validation_passed,
            message=message,
            recommendations=recommendations
        )

    def suggest_optimal_config(
        self,
        strategy_name: str,
        account_value: float,
        coins: List[str],
        aggressive: bool = False
    ) -> Dict:
        """Suggest optimal configuration based on account size"""

        risk_multiplier = self.STRATEGY_RISK_MULTIPLIERS.get(strategy_name, 1.0)

        # Conservative vs aggressive allocation
        allocation_pct = 0.5 if aggressive else 0.3
        usable_balance = account_value * allocation_pct

        # Calculate optimal parameters
        if strategy_name == 'grid_trading':
            # Grid trading needs special handling
            grid_levels = 5 if not aggressive else 10
            max_positions = 3 if not aggressive else 5
            position_size = max(
                self.MIN_ORDER_VALUES['default'],
                usable_balance / (grid_levels * self.MARGIN_REQUIREMENT * self.SAFETY_BUFFER * risk_multiplier)
            )

            return {
                'grid_levels': grid_levels,
                'position_size_per_grid': round(position_size, 0),
                'max_positions': max_positions,
                'grid_spacing_pct': 0.5 if not aggressive else 0.3
            }
        else:
            # Standard strategies
            max_positions = len(coins) if not aggressive else len(coins) * 2
            max_positions = min(max_positions, 5)  # Cap at 5

            position_size = max(
                self.MIN_ORDER_VALUES['default'],
                usable_balance / (max_positions * self.MARGIN_REQUIREMENT * self.SAFETY_BUFFER * risk_multiplier)
            )

            # Round to nice numbers
            if position_size > 100:
                position_size = round(position_size / 10) * 10
            else:
                position_size = round(position_size, 0)

            return {
                'position_size_usd': position_size,
                'max_positions': max_positions
            }

    def validate_minimum_requirements(self) -> ValidationResult:
        """Check if account meets minimum requirements for any trading"""

        account_value, available_balance = self.get_account_info()

        # Minimum viable account balance
        min_required = self.MIN_ORDER_VALUES['default'] * self.MARGIN_REQUIREMENT * self.SAFETY_BUFFER

        if account_value < min_required:
            return ValidationResult(
                is_valid=False,
                message=f"Account balance ${account_value:.2f} is below minimum ${min_required:.2f}",
                recommendations=[
                    f"Add at least ${min_required - account_value:.2f} to your account",
                    "Consider using testnet for practice"
                ]
            )

        return ValidationResult(
            is_valid=True,
            message=f"Account meets minimum requirements (${account_value:.2f} >= ${min_required:.2f})"
        )