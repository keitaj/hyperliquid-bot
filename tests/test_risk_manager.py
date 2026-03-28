"""Tests for RiskManager: check_risk_limits, per-trade stop loss, cooldown, risk levels."""

import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from risk_manager import RiskManager, RiskMetrics


def _make_config(**overrides):
    base = {
        'max_leverage': 3.0,
        'max_position_size_pct': 0.2,
        'max_drawdown_pct': 0.1,
        'daily_loss_limit_pct': 0.05,
        'max_position_pct': 0.2,
        'max_margin_usage': 0.8,
        'force_close_margin': None,
        'daily_loss_limit': None,
        'per_trade_stop_loss': None,
        'max_open_positions': 5,
        'cooldown_after_stop': 3600,
        'metrics_cache_ttl': 0,  # disable cache for deterministic tests
    }
    base.update(overrides)
    return base


def _make_metrics(**overrides):
    base = {
        'total_balance': 10000.0,
        'available_balance': 8000.0,
        'margin_used': 2000.0,
        'total_position_value': 5000.0,
        'unrealized_pnl': 100.0,
        'realized_pnl': 0.0,
        'leverage': 0.5,
        'margin_ratio': 0.2,
        'num_positions': 2,
        'timestamp': datetime.now(),
    }
    base.update(overrides)
    return RiskMetrics(**base)


def _make_rm(config_overrides=None, metrics=None):
    """Create a RiskManager with mocked API calls."""
    config = _make_config(**(config_overrides or {}))
    rm = RiskManager(info=MagicMock(), account_address="0xtest", config=config)
    if metrics is not None:
        rm._get_cached_metrics = MagicMock(return_value=metrics)
    return rm


# ------------------------------------------------------------------ #
#  check_risk_limits
# ------------------------------------------------------------------ #

class TestCheckRiskLimits:

    def test_all_checks_pass(self):
        rm = _make_rm(metrics=_make_metrics())
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['all_checks_passed'] is True
        assert result['action'] == 'none'

    def test_leverage_too_high(self):
        rm = _make_rm(metrics=_make_metrics(leverage=5.0))
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['all_checks_passed'] is False
        assert result['leverage_ok'] is False
        assert 'Leverage too high' in result['reason']

    def test_margin_ratio_too_high(self):
        rm = _make_rm(metrics=_make_metrics(margin_ratio=0.9))
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['all_checks_passed'] is False
        assert result['margin_ratio_ok'] is False

    def test_max_positions_exceeded(self):
        rm = _make_rm(
            config_overrides={'max_open_positions': 3},
            metrics=_make_metrics(num_positions=5),
        )
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['all_checks_passed'] is False
        assert result['max_positions_ok'] is False

    def test_force_close_margin_triggers(self):
        rm = _make_rm(
            config_overrides={'force_close_margin': 0.85},
            metrics=_make_metrics(margin_ratio=0.9),
        )
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['force_close_all'] is True
        assert result['action'] == 'force_close'

    def test_force_close_margin_below_threshold(self):
        rm = _make_rm(
            config_overrides={'force_close_margin': 0.95},
            metrics=_make_metrics(margin_ratio=0.2),
        )
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['force_close_all'] is False

    def test_daily_loss_limit_triggers_stop_bot(self):
        rm = _make_rm(
            config_overrides={'daily_loss_limit': 500},
            metrics=_make_metrics(total_balance=9000.0),
        )
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['stop_bot'] is True
        assert result['action'] == 'stop_bot'
        assert 'Daily loss' in result['reason']

    def test_daily_loss_limit_not_triggered_when_profit(self):
        rm = _make_rm(
            config_overrides={'daily_loss_limit': 500},
            metrics=_make_metrics(total_balance=11000.0),
        )
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['stop_bot'] is False

    def test_suspicious_balance_change_ignored(self):
        """Balance drops >50% should be treated as data error, not real loss."""
        rm = _make_rm(
            config_overrides={'daily_loss_limit': 500},
            metrics=_make_metrics(total_balance=3000.0),
        )
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        # Should NOT trigger stop_bot because 70% drop is suspicious
        assert result['stop_bot'] is False

    def test_no_metrics_returns_block(self):
        rm = _make_rm(metrics=None)
        result = rm.check_risk_limits()
        assert result['all_checks_passed'] is False
        assert result['action'] == 'block_new_orders'
        assert 'No metrics' in result['reason']

    def test_drawdown_exceeded(self):
        rm = _make_rm(
            config_overrides={'max_drawdown_pct': 0.05},
            metrics=_make_metrics(total_balance=8000.0),
        )
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['drawdown_ok'] is False


# ------------------------------------------------------------------ #
#  Risk level
# ------------------------------------------------------------------ #

class TestRiskLevel:

    def test_green_level(self):
        rm = _make_rm()
        with patch.dict(os.environ, {'RISK_LEVEL': 'green'}):
            assert rm.get_risk_level() == 'green'
            assert rm.position_size_multiplier() == 1.0

    def test_yellow_level(self):
        rm = _make_rm()
        with patch.dict(os.environ, {'RISK_LEVEL': 'yellow'}):
            assert rm.get_risk_level() == 'yellow'
            assert rm.position_size_multiplier() == 0.5

    def test_red_level_pauses(self):
        rm = _make_rm(metrics=_make_metrics())
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        with patch.dict(os.environ, {'RISK_LEVEL': 'red'}):
            assert rm.position_size_multiplier() == 0.0
            result = rm.check_risk_limits()
            assert result['action'] == 'pause'

    def test_black_level_closes_all(self):
        rm = _make_rm(metrics=_make_metrics())
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        with patch.dict(os.environ, {'RISK_LEVEL': 'black'}):
            result = rm.check_risk_limits()
            assert result['action'] == 'close_all'
            assert result['force_close_all'] is True

    def test_invalid_level_falls_back_to_green(self):
        rm = _make_rm()
        with patch.dict(os.environ, {'RISK_LEVEL': 'invalid'}):
            assert rm.get_risk_level() == 'green'


# ------------------------------------------------------------------ #
#  Cooldown
# ------------------------------------------------------------------ #

class TestCooldown:

    def test_no_cooldown_by_default(self):
        rm = _make_rm()
        assert rm.is_in_cooldown() is False
        assert rm.cooldown_remaining_seconds() == 0.0

    def test_cooldown_after_emergency_stop(self):
        rm = _make_rm(config_overrides={'cooldown_after_stop': 3600})
        rm.record_emergency_stop()
        assert rm.is_in_cooldown() is True
        assert rm.cooldown_remaining_seconds() > 3590

    def test_cooldown_expired(self):
        rm = _make_rm(config_overrides={'cooldown_after_stop': 10})
        rm._emergency_stop_time = datetime.now() - timedelta(seconds=20)
        assert rm.is_in_cooldown() is False
        assert rm.cooldown_remaining_seconds() == 0.0

    def test_cooldown_blocks_trading(self):
        rm = _make_rm(metrics=_make_metrics())
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        rm.record_emergency_stop()
        result = rm.check_risk_limits()
        assert result['all_checks_passed'] is False
        assert result['action'] == 'cooldown'


# ------------------------------------------------------------------ #
#  Per-trade stop loss
# ------------------------------------------------------------------ #

class TestPerTradeStopLoss:

    def test_disabled_returns_empty(self):
        rm = _make_rm(config_overrides={'per_trade_stop_loss': None})
        positions = [{'coin': 'BTC', 'szi': '1.0', 'entryPx': '50000', 'unrealizedPnl': '-5000'}]
        assert rm.check_per_trade_stop_loss(positions) == []

    def test_loss_exceeds_threshold(self):
        rm = _make_rm(config_overrides={'per_trade_stop_loss': 0.05})
        positions = [{
            'coin': 'BTC', 'szi': '1.0', 'entryPx': '50000',
            'unrealizedPnl': '-5000', 'positionValue': '50000',
        }]
        result = rm.check_per_trade_stop_loss(positions)
        assert len(result) == 1
        assert result[0]['coin'] == 'BTC'

    def test_loss_below_threshold(self):
        rm = _make_rm(config_overrides={'per_trade_stop_loss': 0.10})
        positions = [{
            'coin': 'ETH', 'szi': '10.0', 'entryPx': '3000',
            'unrealizedPnl': '-100', 'positionValue': '30000',
        }]
        assert rm.check_per_trade_stop_loss(positions) == []

    def test_profit_position_not_closed(self):
        rm = _make_rm(config_overrides={'per_trade_stop_loss': 0.05})
        positions = [{
            'coin': 'SOL', 'szi': '100.0', 'entryPx': '100',
            'unrealizedPnl': '500', 'positionValue': '10000',
        }]
        assert rm.check_per_trade_stop_loss(positions) == []

    def test_fallback_to_szi_times_entry(self):
        """When positionValue is 0, fall back to abs(szi) * entryPx."""
        rm = _make_rm(config_overrides={'per_trade_stop_loss': 0.05})
        positions = [{
            'coin': 'BTC', 'szi': '-0.5', 'entryPx': '60000',
            'unrealizedPnl': '-5000', 'positionValue': '0',
        }]
        # positionValue = 0.5 * 60000 = 30000, loss = 5000/30000 = 16.7%
        result = rm.check_per_trade_stop_loss(positions)
        assert len(result) == 1

    def test_multiple_positions_mixed(self):
        rm = _make_rm(config_overrides={'per_trade_stop_loss': 0.05})
        positions = [
            {'coin': 'BTC', 'szi': '1.0', 'entryPx': '50000',
             'unrealizedPnl': '-5000', 'positionValue': '50000'},
            {'coin': 'ETH', 'szi': '10.0', 'entryPx': '3000',
             'unrealizedPnl': '100', 'positionValue': '30000'},
            {'coin': 'SOL', 'szi': '200', 'entryPx': '100',
             'unrealizedPnl': '-2000', 'positionValue': '20000'},
        ]
        result = rm.check_per_trade_stop_loss(positions)
        coins = [p['coin'] for p in result]
        assert 'BTC' in coins  # 10% loss
        assert 'SOL' in coins  # 10% loss
        assert 'ETH' not in coins  # profit


# ------------------------------------------------------------------ #
#  Action priority
# ------------------------------------------------------------------ #

class TestActionPriority:
    """Most severe action should win when multiple conditions fire."""

    def test_stop_bot_wins_over_force_close(self):
        rm = _make_rm(
            config_overrides={
                'force_close_margin': 0.85,
                'daily_loss_limit': 500,
            },
            metrics=_make_metrics(margin_ratio=0.9, total_balance=9000.0),
        )
        rm.starting_balance = 10000.0
        rm.daily_starting_balance = 10000.0
        result = rm.check_risk_limits()
        assert result['action'] == 'stop_bot'
        assert result['force_close_all'] is True
        assert result['stop_bot'] is True
