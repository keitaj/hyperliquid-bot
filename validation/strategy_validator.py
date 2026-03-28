"""
Strategy parameter validation for Hyperliquid trading bot.
Validates strategy configuration at startup to catch misconfigurations early.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Valid candle intervals accepted by the Hyperliquid API
VALID_CANDLE_INTERVALS = {'1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '12h', '1d', '1w', '1M'}


def _positive(name: str, value, type_=float) -> List[str]:
    """Validate that value is positive and of expected type."""
    errors = []
    if not isinstance(value, (int, float)):
        errors.append(f"{name}: expected number, got {type(value).__name__}")
        return errors
    if value <= 0:
        errors.append(f"{name}: must be > 0, got {value}")
    return errors


def _positive_int(name: str, value) -> List[str]:
    errors = []
    if not isinstance(value, int):
        errors.append(f"{name}: expected int, got {type(value).__name__}")
        return errors
    if value <= 0:
        errors.append(f"{name}: must be > 0, got {value}")
    return errors


def _range(name: str, value, low: float, high: float) -> List[str]:
    errors = []
    if not isinstance(value, (int, float)):
        errors.append(f"{name}: expected number, got {type(value).__name__}")
        return errors
    if not (low <= value <= high):
        errors.append(f"{name}: must be {low}–{high}, got {value}")
    return errors


def _validate_common(config: Dict) -> List[str]:
    """Validate parameters common to all strategies."""
    errors = []

    if 'position_size_usd' in config:
        errors += _positive('position_size_usd', config['position_size_usd'])
    if 'max_positions' in config:
        errors += _positive_int('max_positions', config['max_positions'])
    if 'take_profit_percent' in config:
        errors += _positive('take_profit_percent', config['take_profit_percent'])
    if 'stop_loss_percent' in config:
        errors += _positive('stop_loss_percent', config['stop_loss_percent'])
    if 'candle_interval' in config:
        val = config['candle_interval']
        if val not in VALID_CANDLE_INTERVALS:
            errors.append(
                f"candle_interval: invalid '{val}', "
                f"must be one of {sorted(VALID_CANDLE_INTERVALS)}"
            )
    if 'account_cap_pct' in config:
        errors += _range('account_cap_pct', config['account_cap_pct'], 0.0, 1.0)

    return errors


def _validate_simple_ma(config: Dict) -> List[str]:
    errors = []
    errors += _positive_int('fast_ma_period', config.get('fast_ma_period', 10))
    errors += _positive_int('slow_ma_period', config.get('slow_ma_period', 30))

    fast = config.get('fast_ma_period', 10)
    slow = config.get('slow_ma_period', 30)
    if isinstance(fast, int) and isinstance(slow, int) and fast >= slow:
        errors.append(f"fast_ma_period ({fast}) must be < slow_ma_period ({slow})")
    return errors


def _validate_rsi(config: Dict) -> List[str]:
    errors = []
    errors += _positive_int('rsi_period', config.get('rsi_period', 14))

    oversold = config.get('oversold_threshold', 30)
    overbought = config.get('overbought_threshold', 70)
    errors += _range('oversold_threshold', oversold, 0, 100)
    errors += _range('overbought_threshold', overbought, 0, 100)
    if isinstance(oversold, (int, float)) and isinstance(overbought, (int, float)):
        if oversold >= overbought:
            errors.append(
                f"oversold_threshold ({oversold}) must be < overbought_threshold ({overbought})"
            )

    extreme_low = config.get('rsi_extreme_low', 25)
    moderate_low = config.get('rsi_moderate_low', 35)
    errors += _range('rsi_extreme_low', extreme_low, 0, 100)
    errors += _range('rsi_moderate_low', moderate_low, 0, 100)
    if isinstance(extreme_low, (int, float)) and isinstance(moderate_low, (int, float)):
        if extreme_low >= moderate_low:
            errors.append(
                f"rsi_extreme_low ({extreme_low}) must be < rsi_moderate_low ({moderate_low})"
            )

    if 'size_multiplier_extreme' in config:
        errors += _positive('size_multiplier_extreme', config['size_multiplier_extreme'])
    if 'size_multiplier_moderate' in config:
        errors += _positive('size_multiplier_moderate', config['size_multiplier_moderate'])
    return errors


def _validate_bollinger_bands(config: Dict) -> List[str]:
    errors = []
    errors += _positive_int('bb_period', config.get('bb_period', 20))
    errors += _positive('std_dev', config.get('std_dev', 2))
    errors += _positive('squeeze_threshold', config.get('squeeze_threshold', 0.02))

    if 'volatility_expansion_threshold' in config:
        errors += _positive('volatility_expansion_threshold', config['volatility_expansion_threshold'])
    if 'high_band_width_threshold' in config:
        errors += _positive('high_band_width_threshold', config['high_band_width_threshold'])
    if 'high_band_width_multiplier' in config:
        errors += _positive('high_band_width_multiplier', config['high_band_width_multiplier'])
    if 'low_band_width_threshold' in config:
        errors += _positive('low_band_width_threshold', config['low_band_width_threshold'])
    if 'low_band_width_multiplier' in config:
        errors += _positive('low_band_width_multiplier', config['low_band_width_multiplier'])
    return errors


def _validate_macd(config: Dict) -> List[str]:
    errors = []
    fast = config.get('fast_ema', 12)
    slow = config.get('slow_ema', 26)
    errors += _positive_int('fast_ema', fast)
    errors += _positive_int('slow_ema', slow)
    errors += _positive_int('signal_ema', config.get('signal_ema', 9))

    if isinstance(fast, int) and isinstance(slow, int) and fast >= slow:
        errors.append(f"fast_ema ({fast}) must be < slow_ema ({slow})")

    if 'divergence_lookback' in config:
        errors += _positive_int('divergence_lookback', config['divergence_lookback'])
    if 'histogram_strength_high' in config:
        errors += _positive('histogram_strength_high', config['histogram_strength_high'])
    if 'histogram_strength_low' in config:
        errors += _positive('histogram_strength_low', config['histogram_strength_low'])
    if 'histogram_multiplier_high' in config:
        errors += _positive('histogram_multiplier_high', config['histogram_multiplier_high'])
    if 'histogram_multiplier_low' in config:
        errors += _positive('histogram_multiplier_low', config['histogram_multiplier_low'])
    return errors


def _validate_grid_trading(config: Dict) -> List[str]:
    errors = []
    errors += _positive_int('grid_levels', config.get('grid_levels', 10))
    errors += _positive('grid_spacing_pct', config.get('grid_spacing_pct', 0.5))
    errors += _positive('position_size_per_grid', config.get('position_size_per_grid', 50))

    if 'range_period' in config:
        errors += _positive_int('range_period', config['range_period'])
    if 'range_pct_threshold' in config:
        errors += _positive('range_pct_threshold', config['range_pct_threshold'])
    if 'volatility_threshold' in config:
        errors += _range('volatility_threshold', config['volatility_threshold'], 0.0, 1.0)
    if 'grid_recalc_bars' in config:
        errors += _positive_int('grid_recalc_bars', config['grid_recalc_bars'])
    if 'grid_saturation_threshold' in config:
        errors += _range('grid_saturation_threshold', config['grid_saturation_threshold'], 0.0, 1.0)

    low = config.get('grid_boundary_margin_low', 0.98)
    high = config.get('grid_boundary_margin_high', 1.02)
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        if low >= high:
            errors.append(
                f"grid_boundary_margin_low ({low}) must be < grid_boundary_margin_high ({high})"
            )
    return errors


def _validate_breakout(config: Dict) -> List[str]:
    errors = []
    errors += _positive_int('lookback_period', config.get('lookback_period', 20))
    errors += _positive('volume_multiplier', config.get('volume_multiplier', 1.5))
    errors += _positive_int('breakout_confirmation_bars', config.get('breakout_confirmation_bars', 2))
    errors += _positive_int('atr_period', config.get('atr_period', 14))

    if 'pivot_window' in config:
        errors += _positive_int('pivot_window', config['pivot_window'])
    if 'avg_volume_lookback' in config:
        errors += _positive_int('avg_volume_lookback', config['avg_volume_lookback'])
    if 'stop_loss_atr_multiplier' in config:
        errors += _positive('stop_loss_atr_multiplier', config['stop_loss_atr_multiplier'])
    if 'position_stop_loss_atr_multiplier' in config:
        errors += _positive('position_stop_loss_atr_multiplier', config['position_stop_loss_atr_multiplier'])
    if 'strong_breakout_multiplier' in config:
        errors += _positive('strong_breakout_multiplier', config['strong_breakout_multiplier'])
    if 'high_atr_multiplier' in config:
        errors += _positive('high_atr_multiplier', config['high_atr_multiplier'])
    if 'low_atr_multiplier' in config:
        errors += _positive('low_atr_multiplier', config['low_atr_multiplier'])

    high_atr = config.get('high_atr_threshold')
    low_atr = config.get('low_atr_threshold')
    if high_atr is not None:
        errors += _positive('high_atr_threshold', high_atr)
    if low_atr is not None:
        errors += _positive('low_atr_threshold', low_atr)
    if (high_atr is not None and low_atr is not None
            and isinstance(high_atr, (int, float)) and isinstance(low_atr, (int, float))):
        if low_atr >= high_atr:
            errors.append(
                f"low_atr_threshold ({low_atr}) must be < high_atr_threshold ({high_atr})"
            )
    return errors


def _validate_market_making(config: Dict) -> List[str]:
    errors = []
    errors += _positive('spread_bps', config.get('spread_bps', 5))
    errors += _positive('order_size_usd', config.get('order_size_usd', 50))

    if 'max_open_orders' in config:
        errors += _positive_int('max_open_orders', config['max_open_orders'])
    if 'refresh_interval_seconds' in config:
        errors += _positive('refresh_interval_seconds', config['refresh_interval_seconds'])
    if 'max_position_age_seconds' in config:
        errors += _positive('max_position_age_seconds', config['max_position_age_seconds'])
    if 'taker_fallback_age_seconds' in config:
        val = config['taker_fallback_age_seconds']
        if val is not None:
            if not isinstance(val, (int, float)):
                errors.append(f"taker_fallback_age_seconds: expected number, got {type(val).__name__}")
            elif val < 0:
                errors.append(f"taker_fallback_age_seconds: must be >= 0, got {val}")
    return errors


_STRATEGY_VALIDATORS = {
    'simple_ma': _validate_simple_ma,
    'rsi': _validate_rsi,
    'bollinger_bands': _validate_bollinger_bands,
    'macd': _validate_macd,
    'grid_trading': _validate_grid_trading,
    'breakout': _validate_breakout,
    'market_making': _validate_market_making,
}


def validate_strategy_config(strategy_name: str, config: Dict) -> Optional[str]:
    """Validate strategy configuration and return error message if invalid.

    Returns None if configuration is valid, or a formatted error string
    describing all validation failures.
    """
    errors = _validate_common(config)

    validator = _STRATEGY_VALIDATORS.get(strategy_name)
    if validator:
        errors += validator(config)

    if not errors:
        return None

    lines = [f"Invalid {strategy_name} strategy configuration:"]
    for err in errors:
        lines.append(f"  - {err}")
    return "\n".join(lines)
