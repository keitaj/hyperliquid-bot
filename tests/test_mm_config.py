"""Unit tests for ``strategies.mm_config``."""

import pytest

from strategies.mm_config import (
    DYNAMIC_AGE_LOG_INTERVAL,
    FILL_RATE_LOG_INTERVAL,
    INVENTORY_SKEW_CAP,
    LossStreakConfig,
    MicropriceConfig,
    MMConfig,
    PerCoinOverrides,
    VelocityGuardConfig,
    parse_coin_overrides,
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

    def test_independent_dicts(self) -> None:
        # Default factory must produce a fresh dict per instance
        a = PerCoinOverrides()
        b = PerCoinOverrides()
        a.offset['BTC'] = 1.0
        assert 'BTC' not in b.offset


class TestMMConfigFromLegacyDict:
    def test_empty_dict_yields_defaults(self) -> None:
        cfg = MMConfig.from_legacy_dict({})
        assert cfg.loss_streak.limit == 0
        assert cfg.microprice.enabled is False
        assert cfg.velocity.enabled is False
        assert cfg.per_coin.offset == {}

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
