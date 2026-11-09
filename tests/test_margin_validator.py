"""Tests for MarginValidator.validate_strategy_config sizing logic.

The validator must validate the order size the strategy will actually send:
- market_making configures `order_size_usd` (not `position_size_usd`)
- at runtime every order is capped at account_cap_pct x account value
  (BaseStrategy._apply_account_cap)

Regression target: a bot that would run fine under its account cap was
refused at startup because validation used the generic $100 default size.
"""

from unittest.mock import Mock, patch

from validation.margin_validator import MarginValidator


def make_validator(account_value: float) -> MarginValidator:
    validator = MarginValidator(info=Mock(), account_address="0xtest")
    # Pin constants so tests do not depend on environment overrides
    validator.MIN_ORDER_VALUES = {"default": 10.0}
    validator.INITIAL_MARGIN_MULTIPLIER = 3.0
    validator.SAFETY_BUFFER = 1.5
    patch.object(
        validator, "get_account_info", return_value=(account_value, account_value)
    ).start()
    return validator


COINS = ["BTC"]
PRICES = {"BTC": 100.0}


class TestEffectiveSizeValidation:
    def test_account_cap_allows_start_on_low_balance(self):
        """Capped size must be validated, not the raw configured size.

        With account_cap_pct, runtime orders shrink with the account, so the
        margin requirement is computed from the capped size and validation
        passes even when the uncapped size would fail.
        """
        validator = make_validator(account_value=268.43)
        result = validator.validate_strategy_config(
            strategy_name="market_making",
            strategy_config={
                "order_size_usd": 130,
                "max_positions": 6,
                "account_cap_pct": 0.25,
            },
            coins=COINS,
            current_prices=PRICES,
        )
        # capped size = 268.43 * 0.25 = 67.11
        # required = 67.11 * 6 * 0.1 * 3.0 * 1.5 = 181.20 < 268.43
        assert result.is_valid

    def test_no_cap_preserves_old_behavior(self):
        """Without account_cap_pct in the config, nothing is clamped."""
        validator = make_validator(account_value=268.43)
        result = validator.validate_strategy_config(
            strategy_name="market_making",
            strategy_config={"max_positions": 6},
            coins=COINS,
            current_prices=PRICES,
        )
        # default size 100: required = 100 * 6 * 0.45 = 270.00 > 268.43
        assert not result.is_valid

    def test_order_size_usd_is_used_when_position_size_usd_absent(self):
        """market_making configures order_size_usd; the validator must see it."""
        validator = make_validator(account_value=1000.0)
        result = validator.validate_strategy_config(
            strategy_name="market_making",
            strategy_config={"order_size_usd": 500, "max_positions": 6},
            coins=COINS,
            current_prices=PRICES,
        )
        # required = 500 * 6 * 0.45 = 1350 > 1000; the old $100 default would
        # have passed (270 < 1000), hiding a genuinely oversized config
        assert not result.is_valid

    def test_position_size_usd_takes_precedence(self):
        validator = make_validator(account_value=1000.0)
        result = validator.validate_strategy_config(
            strategy_name="simple_ma",
            strategy_config={
                "position_size_usd": 50,
                "order_size_usd": 500,
                "max_positions": 6,
            },
            coins=COINS,
            current_prices=PRICES,
        )
        # required = 50 * 6 * 0.45 = 135 < 1000
        assert result.is_valid

    def test_cap_below_exchange_minimum_still_fails(self):
        """The real floor remains: if the cap shrinks orders below the exchange
        minimum order value, the bot genuinely cannot trade and must not start.
        """
        validator = make_validator(account_value=30.0)
        result = validator.validate_strategy_config(
            strategy_name="market_making",
            strategy_config={
                "order_size_usd": 130,
                "max_positions": 6,
                "account_cap_pct": 0.25,
            },
            coins=COINS,
            current_prices=PRICES,
        )
        # capped size = 30 * 0.25 = 7.50 < $10 minimum order value
        assert not result.is_valid
