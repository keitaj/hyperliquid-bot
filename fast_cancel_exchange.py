"""Exchange subclass that sends cancels with the Hyperliquid ``fast`` flag.

Hyperliquid's ``cancel`` and ``cancelByCloid`` actions accept an optional
top-level ``"f": true`` ("fast") field. The optimizing-latency docs recommend
sending every cancel with it: "There is no disadvantage to the fast flag,
except that fast cancels cannot be used to cancel trigger orders." Per the
exchange-endpoint docs, in a future network upgrade cancel actions will be
prioritized in the mempool if and only if ``fast = true``; today the flag has
no other effect. The field MUST be omitted when false: an action hashed with
``"f": false`` is rejected.

``hyperliquid-python-sdk`` (through 0.24.0) builds the cancel action without
``f``. Because the action is signed before posting, the flag has to be injected
into the action dict *before* ``sign_l1_action`` runs, which is why this is a
subclass override rather than a call-site wrapper. The method bodies mirror the
SDK's ``bulk_cancel`` / ``bulk_cancel_by_cloid`` with the single added line.

The market-making strategy never places trigger orders, so fast cancels are
always safe here. ``fast_cancel`` is a class attribute so it can be disabled
without config plumbing if a future strategy introduces trigger orders.
"""
from typing import Any, List

from hyperliquid.exchange import Exchange
from hyperliquid.utils.constants import MAINNET_API_URL
from hyperliquid.utils.signing import get_timestamp_ms, sign_l1_action


class FastCancelExchange(Exchange):
    """``Exchange`` whose cancel actions carry ``"f": true``."""

    #: Set to ``False`` to send plain (non-fast) cancels. When false the ``f``
    #: key is omitted entirely, as the protocol requires.
    fast_cancel: bool = True

    def _apply_fast_flag(self, action: dict) -> dict:
        if self.fast_cancel:
            action["f"] = True
        return action

    def bulk_cancel(self, cancel_requests: List[dict]) -> Any:
        timestamp = get_timestamp_ms()
        cancel_action = {
            "type": "cancel",
            "cancels": [
                {
                    "a": self.info.name_to_asset(cancel["coin"]),
                    "o": cancel["oid"],
                }
                for cancel in cancel_requests
            ],
        }
        self._apply_fast_flag(cancel_action)
        signature = sign_l1_action(
            self.wallet,
            cancel_action,
            self.vault_address,
            timestamp,
            self.expires_after,
            self.base_url == MAINNET_API_URL,
        )
        return self._post_action(cancel_action, signature, timestamp)

    def bulk_cancel_by_cloid(self, cancel_requests: List[dict]) -> Any:
        timestamp = get_timestamp_ms()
        cancel_action = {
            "type": "cancelByCloid",
            "cancels": [
                {
                    "asset": self.info.name_to_asset(cancel["coin"]),
                    "cloid": cancel["cloid"].to_raw(),
                }
                for cancel in cancel_requests
            ],
        }
        self._apply_fast_flag(cancel_action)
        signature = sign_l1_action(
            self.wallet,
            cancel_action,
            self.vault_address,
            timestamp,
            self.expires_after,
            self.base_url == MAINNET_API_URL,
        )
        return self._post_action(cancel_action, signature, timestamp)
