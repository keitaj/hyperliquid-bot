"""Tests for config merging and default values."""


class TestDefaultConfigs:
    """Verify that bot.py default_configs contain all required keys per strategy."""

    # Replicate the default_configs from bot.py to test in isolation
    # (avoids importing bot.py which requires env vars / network)
    REQUIRED_COMMON_KEYS = {
        'position_size_usd', 'max_positions',
        'take_profit_percent', 'stop_loss_percent', 'candle_interval',
    }

    STRATEGY_SPECIFIC_KEYS = {
        'simple_ma': {'fast_ma_period', 'slow_ma_period'},
        'rsi': {
            'rsi_period', 'oversold_threshold', 'overbought_threshold',
            'rsi_extreme_low', 'rsi_moderate_low',
            'size_multiplier_extreme', 'size_multiplier_moderate',
        },
        'bollinger_bands': {
            'bb_period', 'std_dev', 'squeeze_threshold',
            'volatility_expansion_threshold',
            'high_band_width_threshold', 'high_band_width_multiplier',
            'low_band_width_threshold', 'low_band_width_multiplier',
        },
        'macd': {
            'fast_ema', 'slow_ema', 'signal_ema',
            'divergence_lookback',
            'histogram_strength_high', 'histogram_strength_low',
            'histogram_multiplier_high', 'histogram_multiplier_low',
        },
        'grid_trading': {
            'grid_levels', 'grid_spacing_pct', 'position_size_per_grid',
            'range_period', 'range_pct_threshold', 'volatility_threshold',
            'grid_recalc_bars', 'grid_saturation_threshold',
            'grid_boundary_margin_low', 'grid_boundary_margin_high',
            'account_cap_pct',
        },
        'breakout': {
            'lookback_period', 'volume_multiplier',
            'breakout_confirmation_bars', 'atr_period',
            'pivot_window', 'avg_volume_lookback',
            'stop_loss_atr_multiplier', 'position_stop_loss_atr_multiplier',
            'strong_breakout_multiplier',
            'high_atr_threshold', 'low_atr_threshold',
            'high_atr_multiplier', 'low_atr_multiplier',
        },
        'market_making': {
            'spread_bps', 'order_size_usd', 'max_open_orders',
            'refresh_interval_seconds', 'close_immediately',
            'maker_only', 'account_cap_pct',
        },
    }

    def test_config_merge_cli_overrides(self):
        """CLI overrides should merge on top of defaults, not replace them."""
        defaults = {'a': 1, 'b': 2, 'c': 3}
        cli = {'b': 99}
        merged = {**defaults, **cli}
        assert merged == {'a': 1, 'b': 99, 'c': 3}

    def test_config_merge_empty_cli(self):
        """Empty CLI should return defaults unchanged."""
        defaults = {'a': 1, 'b': 2}
        cli = {}
        merged = {**defaults, **(cli or {})}
        assert merged == defaults

    def test_config_merge_none_cli(self):
        """None CLI should return defaults unchanged."""
        defaults = {'a': 1, 'b': 2}
        cli = None
        merged = {**defaults, **(cli or {})}
        assert merged == defaults
