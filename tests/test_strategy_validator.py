"""Tests for strategy parameter validation."""

from validation.strategy_validator import validate_strategy_config, VALID_CANDLE_INTERVALS


class TestCommonValidation:
    """Validate parameters shared across all strategies."""

    def test_valid_defaults_pass(self):
        assert validate_strategy_config('simple_ma', {
            'fast_ma_period': 10, 'slow_ma_period': 30,
            'position_size_usd': 100, 'max_positions': 3,
            'take_profit_percent': 5, 'stop_loss_percent': 2,
            'candle_interval': '5m',
        }) is None

    def test_invalid_candle_interval(self):
        err = validate_strategy_config('simple_ma', {
            'fast_ma_period': 10, 'slow_ma_period': 30,
            'candle_interval': '2m',
        })
        assert err is not None
        assert 'candle_interval' in err

    def test_all_valid_candle_intervals_accepted(self):
        for interval in VALID_CANDLE_INTERVALS:
            assert validate_strategy_config('simple_ma', {
                'fast_ma_period': 10, 'slow_ma_period': 30,
                'candle_interval': interval,
            }) is None

    def test_negative_position_size(self):
        err = validate_strategy_config('simple_ma', {
            'fast_ma_period': 10, 'slow_ma_period': 30,
            'position_size_usd': -100,
        })
        assert err is not None
        assert 'position_size_usd' in err

    def test_zero_max_positions(self):
        err = validate_strategy_config('simple_ma', {
            'fast_ma_period': 10, 'slow_ma_period': 30,
            'max_positions': 0,
        })
        assert err is not None
        assert 'max_positions' in err

    def test_account_cap_pct_out_of_range(self):
        err = validate_strategy_config('grid_trading', {
            'account_cap_pct': 1.5,
        })
        assert err is not None
        assert 'account_cap_pct' in err


class TestSimpleMAValidation:

    def test_fast_ge_slow_rejected(self):
        err = validate_strategy_config('simple_ma', {
            'fast_ma_period': 30, 'slow_ma_period': 10,
        })
        assert err is not None
        assert 'fast_ma_period' in err

    def test_fast_eq_slow_rejected(self):
        err = validate_strategy_config('simple_ma', {
            'fast_ma_period': 20, 'slow_ma_period': 20,
        })
        assert err is not None
        assert 'fast_ma_period' in err

    def test_negative_period_rejected(self):
        err = validate_strategy_config('simple_ma', {
            'fast_ma_period': -5, 'slow_ma_period': 30,
        })
        assert err is not None
        assert 'fast_ma_period' in err


class TestRSIValidation:

    def test_valid_rsi_config(self):
        assert validate_strategy_config('rsi', {
            'rsi_period': 14, 'oversold_threshold': 30,
            'overbought_threshold': 70,
        }) is None

    def test_oversold_ge_overbought_rejected(self):
        err = validate_strategy_config('rsi', {
            'oversold_threshold': 80, 'overbought_threshold': 20,
        })
        assert err is not None
        assert 'oversold_threshold' in err

    def test_threshold_out_of_range(self):
        err = validate_strategy_config('rsi', {
            'oversold_threshold': -10,
        })
        assert err is not None
        assert 'oversold_threshold' in err

    def test_extreme_low_ge_moderate_low_rejected(self):
        err = validate_strategy_config('rsi', {
            'rsi_extreme_low': 40, 'rsi_moderate_low': 30,
        })
        assert err is not None
        assert 'rsi_extreme_low' in err


class TestMACDValidation:

    def test_valid_macd_config(self):
        assert validate_strategy_config('macd', {
            'fast_ema': 12, 'slow_ema': 26, 'signal_ema': 9,
        }) is None

    def test_fast_ge_slow_rejected(self):
        err = validate_strategy_config('macd', {
            'fast_ema': 26, 'slow_ema': 12,
        })
        assert err is not None
        assert 'fast_ema' in err


class TestBollingerBandsValidation:

    def test_valid_config(self):
        assert validate_strategy_config('bollinger_bands', {
            'bb_period': 20, 'std_dev': 2, 'squeeze_threshold': 0.02,
        }) is None

    def test_zero_std_dev_rejected(self):
        err = validate_strategy_config('bollinger_bands', {
            'std_dev': 0,
        })
        assert err is not None
        assert 'std_dev' in err


class TestGridTradingValidation:

    def test_valid_config(self):
        assert validate_strategy_config('grid_trading', {
            'grid_levels': 10, 'grid_spacing_pct': 0.5,
            'grid_boundary_margin_low': 0.98, 'grid_boundary_margin_high': 1.02,
        }) is None

    def test_boundary_low_ge_high_rejected(self):
        err = validate_strategy_config('grid_trading', {
            'grid_boundary_margin_low': 1.05, 'grid_boundary_margin_high': 0.95,
        })
        assert err is not None
        assert 'grid_boundary_margin_low' in err

    def test_volatility_threshold_out_of_range(self):
        err = validate_strategy_config('grid_trading', {
            'volatility_threshold': 2.0,
        })
        assert err is not None
        assert 'volatility_threshold' in err


class TestBreakoutValidation:

    def test_valid_config(self):
        assert validate_strategy_config('breakout', {
            'lookback_period': 20, 'volume_multiplier': 1.5,
            'breakout_confirmation_bars': 2, 'atr_period': 14,
        }) is None

    def test_low_atr_ge_high_atr_rejected(self):
        err = validate_strategy_config('breakout', {
            'low_atr_threshold': 5.0, 'high_atr_threshold': 2.0,
        })
        assert err is not None
        assert 'low_atr_threshold' in err


class TestMarketMakingValidation:

    def test_valid_config(self):
        assert validate_strategy_config('market_making', {
            'spread_bps': 5, 'order_size_usd': 50,
        }) is None

    def test_zero_spread_rejected(self):
        err = validate_strategy_config('market_making', {
            'spread_bps': 0,
        })
        assert err is not None
        assert 'spread_bps' in err

    def test_negative_taker_fallback_rejected(self):
        err = validate_strategy_config('market_making', {
            'taker_fallback_age_seconds': -1,
        })
        assert err is not None
        assert 'taker_fallback_age_seconds' in err

    def test_taker_fallback_zero_accepted(self):
        assert validate_strategy_config('market_making', {
            'taker_fallback_age_seconds': 0,
        }) is None

    def test_taker_fallback_none_accepted(self):
        assert validate_strategy_config('market_making', {
            'taker_fallback_age_seconds': None,
        }) is None


class TestMultipleErrors:
    """Validate that multiple errors are reported at once."""

    def test_reports_all_errors(self):
        err = validate_strategy_config('simple_ma', {
            'fast_ma_period': -1,
            'slow_ma_period': -2,
            'position_size_usd': 0,
            'candle_interval': 'invalid',
        })
        assert err is not None
        assert 'fast_ma_period' in err
        assert 'slow_ma_period' in err
        assert 'position_size_usd' in err
        assert 'candle_interval' in err


class TestUnknownStrategy:
    """Unknown strategy names should still validate common params."""

    def test_unknown_strategy_common_validation(self):
        err = validate_strategy_config('unknown_strategy', {
            'position_size_usd': -100,
        })
        assert err is not None
        assert 'position_size_usd' in err

    def test_unknown_strategy_valid_common(self):
        assert validate_strategy_config('unknown_strategy', {
            'position_size_usd': 100,
        }) is None
