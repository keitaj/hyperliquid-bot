"""Tests for risk check throttling in _trading_loop."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def bot():
    """Create a minimal HyperliquidBot with mocked dependencies."""
    with patch('bot.Config') as MockConfig, \
         patch('bot.Exchange'), \
         patch('bot.signal'):
        MockConfig.USE_TESTNET = False
        MockConfig.API_URL = "https://api.hyperliquid.xyz"
        MockConfig.ACCOUNT_ADDRESS = "0x0000000000000000000000000000000000000000"
        MockConfig.ENABLE_STANDARD_HL = False
        MockConfig.TRADING_DEXES = []
        MockConfig.API_TIMEOUT = 10
        MockConfig.RISK_CHECK_INTERVAL = 10.0
        MockConfig.META_CACHE_TTL = 3600
        MockConfig.MIDS_CACHE_TTL = 5

        from bot import HyperliquidBot
        b = HyperliquidBot.__new__(HyperliquidBot)
        b.risk_manager = MagicMock()
        b.order_manager = MagicMock()
        b.strategy = MagicMock()
        b.circuit_breaker = MagicMock()
        b.circuit_breaker.is_tripped.return_value = False
        b.coins = ['BTC']
        b.main_loop_interval = 3
        b._risk_check_interval = 10.0
        b._last_risk_check = 0.0
        b._last_risk_result = {'all_checks_passed': False, 'action': 'none'}
        b.adverse_tracker = None

        b.risk_manager.check_risk_limits.return_value = {
            'all_checks_passed': True,
            'action': 'none',
            'reason': '',
        }
        return b


class TestRiskCheckThrottle:

    @patch('time.time', return_value=1000.0)
    def test_first_cycle_always_checks(self, mock_time, bot):
        """First cycle runs risk check because _last_risk_check=0."""
        bot._trading_loop()
        bot.risk_manager.check_risk_limits.assert_called_once()
        assert bot._last_risk_check == 1000.0

    @patch('time.time', return_value=1000.0)
    def test_cached_result_within_interval(self, mock_time, bot):
        """Within interval, cached result is used without API call."""
        bot._last_risk_check = 995.0  # 5s ago, within 10s interval
        bot._last_risk_result = {'all_checks_passed': True, 'action': 'none'}
        bot._trading_loop()
        bot.risk_manager.check_risk_limits.assert_not_called()

    @patch('time.time', return_value=1000.0)
    def test_check_runs_after_interval(self, mock_time, bot):
        """After interval elapses, risk check runs again."""
        bot._last_risk_check = 989.0  # 11s ago, past 10s interval
        bot._last_risk_result = {'all_checks_passed': True, 'action': 'none'}
        bot._trading_loop()
        bot.risk_manager.check_risk_limits.assert_called_once()
        assert bot._last_risk_check == 1000.0

    @patch('time.time', return_value=1000.0)
    def test_fail_safe_default_blocks_until_first_check(self, mock_time, bot):
        """Default _last_risk_result has all_checks_passed=False."""
        bot._last_risk_check = 999.0  # within interval, use cached
        # Default is False — should cancel orders
        bot._last_risk_result = {'all_checks_passed': False, 'action': 'none', 'reason': 'initial'}
        bot._trading_loop()
        bot.order_manager.cancel_all_orders.assert_called()
