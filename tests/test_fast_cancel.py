"""Tests for FastCancelExchange: cancels must carry the ``f`` (fast) flag.

The SDK ``Exchange.__init__`` constructs an ``Info`` that fetches exchange
metadata over the network, so the instance is built with ``__new__`` and the
attributes the cancel methods read are set directly. ``sign_l1_action`` and
``_post_action`` are mocked; the assertions inspect the *signed* action dict,
which is what the exchange hashes and validates.
"""
from unittest.mock import MagicMock, patch

import pytest

from fast_cancel_exchange import FastCancelExchange
from hyperliquid.utils.constants import MAINNET_API_URL


def _make_exchange(fast=True):
    ex = FastCancelExchange.__new__(FastCancelExchange)
    ex.wallet = MagicMock()
    ex.vault_address = None
    ex.expires_after = None
    ex.base_url = MAINNET_API_URL
    ex.info = MagicMock()
    ex.info.name_to_asset.side_effect = lambda coin: {"xyz:SP500": 110000, "xyz:XYZ100": 110001}[coin]
    ex._post_action = MagicMock(return_value={"status": "ok"})
    ex.fast_cancel = fast
    return ex


@patch("fast_cancel_exchange.sign_l1_action", return_value="sig")
def test_bulk_cancel_sets_fast_flag(mock_sign):
    ex = _make_exchange()
    result = ex.bulk_cancel([{"coin": "xyz:SP500", "oid": 1}, {"coin": "xyz:XYZ100", "oid": 2}])

    assert result == {"status": "ok"}
    signed_action = mock_sign.call_args.args[1]
    assert signed_action["type"] == "cancel"
    assert signed_action["cancels"] == [{"a": 110000, "o": 1}, {"a": 110001, "o": 2}]
    assert signed_action["f"] is True
    # the posted action is the same object that was signed
    posted_action = ex._post_action.call_args.args[0]
    assert posted_action is signed_action


@patch("fast_cancel_exchange.sign_l1_action", return_value="sig")
def test_bulk_cancel_by_cloid_sets_fast_flag(mock_sign):
    ex = _make_exchange()
    cloid = MagicMock()
    cloid.to_raw.return_value = "0x" + "ab" * 16
    ex.bulk_cancel_by_cloid([{"coin": "xyz:SP500", "cloid": cloid}])

    signed_action = mock_sign.call_args.args[1]
    assert signed_action["type"] == "cancelByCloid"
    assert signed_action["cancels"] == [{"asset": 110000, "cloid": "0x" + "ab" * 16}]
    assert signed_action["f"] is True


@pytest.mark.parametrize("method", ["bulk_cancel", "bulk_cancel_by_cloid"])
@patch("fast_cancel_exchange.sign_l1_action", return_value="sig")
def test_disabled_fast_flag_omits_key_entirely(mock_sign, method):
    """Protocol rule: ``f`` must be absent when false, never ``f: false``."""
    ex = _make_exchange(fast=False)
    if method == "bulk_cancel":
        ex.bulk_cancel([{"coin": "xyz:SP500", "oid": 7}])
    else:
        cloid = MagicMock()
        cloid.to_raw.return_value = "0x" + "cd" * 16
        ex.bulk_cancel_by_cloid([{"coin": "xyz:SP500", "cloid": cloid}])

    signed_action = mock_sign.call_args.args[1]
    assert "f" not in signed_action


@patch("fast_cancel_exchange.sign_l1_action", return_value="sig")
def test_single_cancel_routes_through_fast_bulk_cancel(mock_sign):
    """``Exchange.cancel`` delegates to ``bulk_cancel``, so it inherits the flag."""
    ex = _make_exchange()
    ex.cancel("xyz:XYZ100", 42)

    signed_action = mock_sign.call_args.args[1]
    assert signed_action["cancels"] == [{"a": 110001, "o": 42}]
    assert signed_action["f"] is True


@patch("fast_cancel_exchange.sign_l1_action", return_value="sig")
def test_signature_uses_mainnet_flag_and_nonce(mock_sign):
    ex = _make_exchange()
    ex.bulk_cancel([{"coin": "xyz:SP500", "oid": 1}])

    args = mock_sign.call_args.args
    assert args[0] is ex.wallet
    assert args[2] is None            # vault_address
    assert isinstance(args[3], int)   # nonce / timestamp ms
    assert args[4] is None            # expires_after
    assert args[5] is True            # is_mainnet
    # nonce passed to _post_action matches the signed nonce
    assert ex._post_action.call_args.args[2] == args[3]
