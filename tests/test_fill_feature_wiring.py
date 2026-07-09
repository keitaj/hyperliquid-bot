"""Wiring tests for the fill feature writer inside HyperliquidBot.

Covers the bot-side branches that the writer unit tests cannot see:
the main-loop ``maybe_flush``, the post-loop shutdown ``flush_all`` on
the internal stop_bot / cooldown exits, and the run()-time warning when
the feature is enabled without its prerequisites.
"""

from unittest.mock import MagicMock, patch


def _make_bot(strategy_config=None):
    with patch('bot.Config') as MockConfig, \
         patch('bot.Exchange'), \
         patch('bot.signal'):
        MockConfig.USE_TESTNET = False
        MockConfig.API_URL = "https://api.hyperliquid.xyz"
        MockConfig.ACCOUNT_ADDRESS = "0x0"
        MockConfig.ENABLE_STANDARD_HL = False
        MockConfig.TRADING_DEXES = []
        MockConfig.API_TIMEOUT = 10
        MockConfig.RISK_CHECK_INTERVAL = 10.0

        from bot import HyperliquidBot
        b = HyperliquidBot.__new__(HyperliquidBot)
        b.risk_manager = MagicMock()
        b.order_manager = MagicMock()
        b.strategy = MagicMock()
        b.circuit_breaker = MagicMock()
        b.circuit_breaker.is_tripped.return_value = False
        b.coins = ['BTC']
        b.main_loop_interval = 0
        b._risk_check_interval = 10.0
        b._last_risk_check = 0.0
        b._last_risk_result = {'all_checks_passed': True, 'action': 'none', 'reason': ''}
        b.adverse_tracker = None
        b.imbalance_guard = None
        b.oracle_guard = None
        b.fill_feature_writer = None
        b.fill_feed = None
        b.ws_feed = None
        b._ws_reconnector = None
        b.last_connection_reset = 0.0
        b.account_address = "0x0"
        b._enable_ws = False
        b.strategy_config = strategy_config if strategy_config is not None else {}
        b.risk_manager.check_risk_limits.return_value = {
            'all_checks_passed': True, 'action': 'none', 'reason': '',
        }
        return b


class TestTradingLoopFlush:
    @patch('time.time', return_value=1000.0)
    def test_maybe_flush_called_on_healthy_cycle(self, _t):
        bot = _make_bot()
        bot.fill_feature_writer = MagicMock()
        bot._trading_loop()
        bot.fill_feature_writer.maybe_flush.assert_called_once()

    @patch('time.time', return_value=1000.0)
    def test_no_crash_when_writer_absent(self, _t):
        bot = _make_bot()
        bot.fill_feature_writer = None
        bot._trading_loop()  # must not raise


class TestShutdownFlush:
    def _run_until_exit(self, bot):
        """Drive run() with pre-loop setup mocked; loop exits on cycle 1."""
        bot.get_user_state = MagicMock(return_value={})
        bot._validate_trading_configuration = MagicMock(return_value=True)

        def _one_cycle():
            bot.running = False  # simulate stop_bot / cooldown exit
        bot._trading_loop = _one_cycle

        with patch('bot.signal'), patch('time.sleep'):
            bot.run()

    def test_flush_all_on_loop_exit(self):
        bot = _make_bot()
        bot.fill_feature_writer = MagicMock()
        writer = bot.fill_feature_writer
        self._run_until_exit(bot)
        writer.flush_all.assert_called_once()

    def test_no_crash_on_loop_exit_without_writer(self):
        bot = _make_bot()
        bot.fill_feature_writer = None
        self._run_until_exit(bot)  # must not raise


class TestPrerequisiteWarning:
    def test_enabled_without_tracker_disables_and_warns(self, caplog):
        import logging
        bot = _make_bot(strategy_config={'fill_feature_log_enabled': True})
        bot.adverse_tracker = None  # prerequisite missing
        bot.get_user_state = MagicMock(return_value={})
        bot._validate_trading_configuration = MagicMock(return_value=True)
        bot._trading_loop = lambda: setattr(bot, 'running', False)

        with patch('bot.signal'), patch('time.sleep'), \
                caplog.at_level(logging.WARNING, logger='bot'):
            bot.run()

        assert bot.fill_feature_writer is None
        assert any('fill_feature_log_enabled requires' in r.message
                   for r in caplog.records)
