"""Unit tests for ``strategies.mm_config``."""

import pytest

from strategies.mm_config import (
    DYNAMIC_AGE_LOG_INTERVAL,
    FILL_RATE_LOG_INTERVAL,
    INVENTORY_SKEW_CAP,
    AutoExcludeConfig,
    CloseConfig,
    DynamicAgeConfig,
    DynamicOffsetConfig,
    ImbalanceConfig,
    LossStreakConfig,
    MicropriceConfig,
    MMConfig,
    PerCoinOverrides,
    ScheduleConfig,
    VelocityGuardConfig,
    parse_coin_overrides,
    parse_quiet_hours,
    parse_spread_schedule,
)


class TestParseCoinOverrides:
    def test_empty(self) -> None:
        assert parse_coin_overrides('') == {}
        assert parse_coin_overrides(None) == {}
        assert parse_coin_overrides('   ') == {}

    def test_single(self) -> None:
        assert parse_coin_overrides('SP500:1.5') == {'SP500': 1.5}

    def test_multiple(self) -> None:
        result = parse_coin_overrides('SP500:0.5,MSFT:3,XYZ100:2')
        assert result == {'SP500': 0.5, 'MSFT': 3.0, 'XYZ100': 2.0}

    def test_dex_prefixed(self) -> None:
        # rsplit preserves "xyz:SP500" as the coin key
        assert parse_coin_overrides('xyz:SP500:0.5') == {'xyz:SP500': 0.5}

    def test_whitespace_around_pairs(self) -> None:
        result = parse_coin_overrides(' SP500:1.5 , MSFT:3 ')
        assert result == {'SP500': 1.5, 'MSFT': 3.0}

    def test_invalid_skipped_silently(self) -> None:
        # Pair without colon — skipped
        assert parse_coin_overrides('SP500,MSFT:3') == {'MSFT': 3.0}
        # Non-numeric BPS — skipped
        assert parse_coin_overrides('SP500:abc,MSFT:3') == {'MSFT': 3.0}
        # Trailing comma — skipped
        assert parse_coin_overrides('SP500:1.5,,MSFT:3,') == {'SP500': 1.5, 'MSFT': 3.0}


class TestLossStreakConfig:
    def test_defaults_are_disabled(self) -> None:
        cfg = LossStreakConfig()
        assert cfg.limit == 0
        assert cfg.cooldown_seconds == 300.0

    def test_negative_limit_rejected(self) -> None:
        with pytest.raises(ValueError, match='loss_streak_limit must be >= 0'):
            LossStreakConfig(limit=-1)

    def test_zero_cooldown_rejected_when_enabled(self) -> None:
        with pytest.raises(ValueError, match='loss_streak_cooldown must be > 0'):
            LossStreakConfig(limit=3, cooldown_seconds=0)

    def test_zero_cooldown_allowed_when_disabled(self) -> None:
        # limit=0 means feature is off; cooldown is irrelevant
        cfg = LossStreakConfig(limit=0, cooldown_seconds=0)
        assert cfg.limit == 0


class TestMicropriceConfig:
    def test_defaults(self) -> None:
        cfg = MicropriceConfig()
        assert cfg.enabled is False
        assert cfg.multiplier == 1.0
        assert cfg.max_skew_bps == 2.0


class TestVelocityGuardConfig:
    def test_defaults(self) -> None:
        cfg = VelocityGuardConfig()
        assert cfg.enabled is False
        assert cfg.consecutive == 3
        assert cfg.min_move_bps == 1.0


class TestPerCoinOverrides:
    def test_defaults_are_empty_dicts(self) -> None:
        cfg = PerCoinOverrides()
        assert cfg.offset == {}
        assert cfg.spread == {}
        assert cfg.size == {}
        assert cfg.unrealized_loss == {}

    def test_independent_dicts(self) -> None:
        # Default factory must produce a fresh dict per instance
        a = PerCoinOverrides()
        b = PerCoinOverrides()
        a.offset['BTC'] = 1.0
        a.unrealized_loss['BTC'] = 25.0
        assert 'BTC' not in b.offset
        assert 'BTC' not in b.unrealized_loss


class TestParseQuietHours:
    def test_empty(self) -> None:
        assert parse_quiet_hours('') == set()
        assert parse_quiet_hours(None) == set()

    def test_single(self) -> None:
        assert parse_quiet_hours('17') == {17}

    def test_multiple_with_whitespace(self) -> None:
        assert parse_quiet_hours(' 17 , 18 ') == {17, 18}

    def test_invalid_skipped(self) -> None:
        assert parse_quiet_hours('17,abc,18') == {17, 18}


class TestParseSpreadSchedule:
    def test_empty(self) -> None:
        assert parse_spread_schedule('') == {}
        assert parse_spread_schedule(None) == {}

    def test_single_hour(self) -> None:
        assert parse_spread_schedule('14:1.5') == {14: 1.5}

    def test_multiple(self) -> None:
        assert parse_spread_schedule('0:1.5,3:2.0,14:1.5') == {0: 1.5, 3: 2.0, 14: 1.5}

    def test_range(self) -> None:
        assert parse_spread_schedule('0-3:1.5') == {0: 1.5, 1: 1.5, 2: 1.5, 3: 1.5}

    def test_range_wrap_around(self) -> None:
        assert parse_spread_schedule('22-2:1.5') == {22: 1.5, 23: 1.5, 0: 1.5, 1: 1.5, 2: 1.5}

    def test_invalid_hour_skipped(self) -> None:
        # 25 is out of range — that entry is skipped, others survive
        assert parse_spread_schedule('25:1.5,14:2.0') == {14: 2.0}

    def test_negative_multiplier_skipped(self) -> None:
        assert parse_spread_schedule('14:-1.5') == {}


class TestImbalanceConfig:
    def test_defaults(self) -> None:
        cfg = ImbalanceConfig()
        assert cfg.placement_threshold == 0.0
        assert cfg.reactive_threshold == 0.0
        assert cfg.reactive_depth == 5

    def test_placement_threshold_range(self) -> None:
        ImbalanceConfig(placement_threshold=0.0)  # ok (boundary)
        ImbalanceConfig(placement_threshold=1.0)  # ok (boundary)
        with pytest.raises(ValueError, match='imbalance_threshold must be in'):
            ImbalanceConfig(placement_threshold=-0.1)
        with pytest.raises(ValueError, match='imbalance_threshold must be in'):
            ImbalanceConfig(placement_threshold=1.1)


class TestCloseConfig:
    def test_defaults(self) -> None:
        cfg = CloseConfig()
        assert cfg.breakeven_pct == 0.50
        assert cfg.aggressive_pct == 0.75
        assert cfg.spread_bps is None
        assert cfg.refresh_threshold_bps == 0.0
        assert cfg.unrealized_loss_close_bps == 0.0
        assert cfg.force_close_max_loss_bps == 0.0


class TestScheduleConfig:
    def test_defaults_are_empty(self) -> None:
        cfg = ScheduleConfig()
        assert cfg.spread_schedule == {}
        assert cfg.quiet_hours_utc == set()
        assert cfg.quiet_hours_spread_multiplier == 0.0


class TestDynamicOffsetConfig:
    def test_defaults_are_disabled(self) -> None:
        cfg = DynamicOffsetConfig()
        assert cfg.enabled is False
        assert cfg.sensitivity == 0.5
        assert cfg.tighten_rate == 0.25
        assert cfg.max_addition == 3.0
        assert cfg.max_reduction == 1.0
        assert cfg.floor == 0.5
        assert cfg.min_fills == 5


class TestDynamicAgeConfig:
    def test_defaults_are_disabled(self) -> None:
        cfg = DynamicAgeConfig()
        assert cfg.enabled is False
        assert cfg.baseline_vol_bps == 1.0
        assert cfg.min_seconds == 60.0
        assert cfg.max_seconds == 300.0


class TestAutoExcludeConfig:
    def test_defaults_are_disabled(self) -> None:
        cfg = AutoExcludeConfig()
        assert cfg.enabled is False
        assert cfg.threshold_bps == -3.0
        assert cfg.consecutive == 3
        assert cfg.min_fills == 3
        assert cfg.cooldown_seconds == 1800
        assert cfg.window_label == '60s'

    def test_invalid_consecutive_rejected(self) -> None:
        with pytest.raises(ValueError, match='auto_exclude_consecutive must be >= 1'):
            AutoExcludeConfig(consecutive=0)

    def test_invalid_min_fills_rejected(self) -> None:
        with pytest.raises(ValueError, match='auto_exclude_min_fills must be >= 1'):
            AutoExcludeConfig(min_fills=0)

    def test_invalid_cooldown_rejected(self) -> None:
        with pytest.raises(ValueError, match='auto_exclude_cooldown must be > 0'):
            AutoExcludeConfig(cooldown_seconds=0)

    def test_invalid_window_label_rejected(self) -> None:
        with pytest.raises(ValueError, match="auto_exclude_window_label must be one of"):
            AutoExcludeConfig(window_label='90s')


class TestMMConfigFromLegacyDict:
    def test_empty_dict_yields_defaults(self) -> None:
        cfg = MMConfig.from_legacy_dict({})
        assert cfg.loss_streak.limit == 0
        assert cfg.microprice.enabled is False
        assert cfg.velocity.enabled is False
        assert cfg.per_coin.offset == {}
        assert cfg.dynamic_offset.enabled is False
        assert cfg.dynamic_age.enabled is False

    def test_full_dict_populates_all_groups(self) -> None:
        d = {
            'loss_streak_limit': 3,
            'loss_streak_cooldown': 600.0,
            'microprice_skew_enabled': True,
            'microprice_skew_multiplier': 1.5,
            'microprice_max_skew_bps': 3.0,
            'velocity_guard_enabled': True,
            'velocity_consecutive': 5,
            'velocity_min_move_bps': 2.0,
            'coin_offset_overrides': 'SP500:0.5,MSFT:3',
            'coin_spread_overrides': 'TSLA:2',
            'coin_size_overrides': 'NVDA:150',
            'coin_unrealized_loss_overrides': 'INTC:25,OIL:10',
            'imbalance_threshold': 0.5,
            'imbalance_guard_threshold': 0.4,
            'imbalance_guard_depth': 7,
            'close_breakeven_pct': 0.6,
            'close_aggressive_pct': 0.8,
            'close_spread_bps': 5.0,
            'close_refresh_threshold_bps': 0.5,
            'unrealized_loss_close_bps': 15.0,
            'force_close_max_loss_bps': 5.0,
            'spread_schedule': '0:1.5,12:1.3',
            'quiet_hours_utc': '17,18',
            'quiet_hours_spread_multiplier': 1.5,
            'dynamic_offset_enabled': True,
            'dynamic_offset_sensitivity': 0.4,
            'dynamic_offset_tighten_rate': 0.2,
            'dynamic_offset_max_addition': 4.0,
            'dynamic_offset_max_reduction': 1.5,
            'dynamic_offset_floor': 0.7,
            'dynamic_offset_min_fills': 10,
            'dynamic_age_enabled': True,
            'dynamic_age_baseline_vol': 1.5,
            'dynamic_age_min': 90.0,
            'dynamic_age_max': 240.0,
            'auto_exclude_enabled': True,
            'auto_exclude_threshold_bps': -2.5,
            'auto_exclude_consecutive': 4,
            'auto_exclude_min_fills': 8,
            'auto_exclude_cooldown': 900,
            'auto_exclude_window_label': '30s',
        }
        cfg = MMConfig.from_legacy_dict(d)
        assert cfg.loss_streak.limit == 3
        assert cfg.loss_streak.cooldown_seconds == 600.0
        assert cfg.microprice.enabled is True
        assert cfg.microprice.multiplier == 1.5
        assert cfg.microprice.max_skew_bps == 3.0
        assert cfg.velocity.enabled is True
        assert cfg.velocity.consecutive == 5
        assert cfg.velocity.min_move_bps == 2.0
        assert cfg.per_coin.offset == {'SP500': 0.5, 'MSFT': 3.0}
        assert cfg.per_coin.spread == {'TSLA': 2.0}
        assert cfg.per_coin.size == {'NVDA': 150.0}
        assert cfg.per_coin.unrealized_loss == {'INTC': 25.0, 'OIL': 10.0}
        assert cfg.imbalance.placement_threshold == 0.5
        assert cfg.imbalance.reactive_threshold == 0.4
        assert cfg.imbalance.reactive_depth == 7
        assert cfg.close.breakeven_pct == 0.6
        assert cfg.close.aggressive_pct == 0.8
        assert cfg.close.spread_bps == 5.0
        assert cfg.close.refresh_threshold_bps == 0.5
        assert cfg.close.unrealized_loss_close_bps == 15.0
        assert cfg.close.force_close_max_loss_bps == 5.0
        assert cfg.schedule.spread_schedule == {0: 1.5, 12: 1.3}
        assert cfg.schedule.quiet_hours_utc == {17, 18}
        assert cfg.schedule.quiet_hours_spread_multiplier == 1.5
        assert cfg.dynamic_offset.enabled is True
        assert cfg.dynamic_offset.sensitivity == 0.4
        assert cfg.dynamic_offset.tighten_rate == 0.2
        assert cfg.dynamic_offset.max_addition == 4.0
        assert cfg.dynamic_offset.max_reduction == 1.5
        assert cfg.dynamic_offset.floor == 0.7
        assert cfg.dynamic_offset.min_fills == 10
        assert cfg.dynamic_age.enabled is True
        assert cfg.dynamic_age.baseline_vol_bps == 1.5
        assert cfg.dynamic_age.min_seconds == 90.0
        assert cfg.dynamic_age.max_seconds == 240.0
        assert cfg.auto_exclude.enabled is True
        assert cfg.auto_exclude.threshold_bps == -2.5
        assert cfg.auto_exclude.consecutive == 4
        assert cfg.auto_exclude.min_fills == 8
        assert cfg.auto_exclude.cooldown_seconds == 900
        assert cfg.auto_exclude.window_label == '30s'

    def test_close_spread_bps_none_when_omitted(self) -> None:
        # Distinguish "not provided" from "0.0" — closer needs Optional[float]
        cfg = MMConfig.from_legacy_dict({})
        assert cfg.close.spread_bps is None

    def test_imbalance_validation_propagates(self) -> None:
        with pytest.raises(ValueError, match='imbalance_threshold'):
            MMConfig.from_legacy_dict({'imbalance_threshold': 1.5})

    def test_unknown_keys_ignored(self) -> None:
        cfg = MMConfig.from_legacy_dict({'unknown_key': 'foo', 'loss_streak_limit': 2})
        assert cfg.loss_streak.limit == 2

    def test_validation_propagates(self) -> None:
        with pytest.raises(ValueError):
            MMConfig.from_legacy_dict({'loss_streak_limit': -1})

    def test_string_to_int_coercion(self) -> None:
        # CLI/env values arrive as strings sometimes
        cfg = MMConfig.from_legacy_dict({'loss_streak_limit': '3', 'velocity_consecutive': '4'})
        assert cfg.loss_streak.limit == 3
        assert cfg.velocity.consecutive == 4

    def test_non_coercible_int_raises(self) -> None:
        # Non-numeric input for an int field surfaces ValueError from int(...)
        with pytest.raises(ValueError):
            MMConfig.from_legacy_dict({'loss_streak_limit': 'abc'})

    def test_non_coercible_float_raises(self) -> None:
        # Same contract for float fields
        with pytest.raises(ValueError):
            MMConfig.from_legacy_dict({'microprice_skew_multiplier': 'not_a_number'})


class TestModuleConstants:
    """Sanity checks for module-level constants.

    Asserts the type and positive-finite property rather than the exact
    default value so intentional default tweaks don't ripple through.
    """

    def test_inventory_skew_cap(self) -> None:
        assert isinstance(INVENTORY_SKEW_CAP, float)
        assert INVENTORY_SKEW_CAP > 0

    def test_fill_rate_log_interval(self) -> None:
        assert isinstance(FILL_RATE_LOG_INTERVAL, float)
        assert FILL_RATE_LOG_INTERVAL > 0

    def test_dynamic_age_log_interval(self) -> None:
        assert isinstance(DYNAMIC_AGE_LOG_INTERVAL, float)
        assert DYNAMIC_AGE_LOG_INTERVAL > 0
