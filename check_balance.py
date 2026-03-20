#!/usr/bin/env python3
"""
Simple script to check Hyperliquid account balance and positions
"""

import sys
import warnings
warnings.filterwarnings("ignore")

import requests
from config import Config

DISPLAY_COINS = {"USDC", "USDH"}
KNOWN_HIP3_DEXES = ["xyz", "flx", "cash"]


def _api_post(req_type: str, address: str, **extra) -> dict:
    payload = {"type": req_type, "user": address, **extra}
    resp = requests.post(
        f"{Config.API_URL}/info",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def _collect_positions(user_state: dict, prefix: str = "") -> list:
    """Extract positions from a clearinghouseState response."""
    result = []
    for pos in user_state.get('assetPositions', []):
        p = pos['position']
        coin = p['coin']
        if prefix and ":" not in coin:
            coin = f"{prefix}:{coin}"
        result.append({
            'coin': coin,
            'size': float(p['szi']),
            'entry_px': float(p.get('entryPx', 0)),
            'unrealized_pnl': float(p.get('unrealizedPnl', 0)),
            'position_value': float(p.get('positionValue', 0)),
            'margin_used': float(p.get('marginUsed', 0)),
        })
    return result


def main():
    try:
        address = Config.ACCOUNT_ADDRESS
        Config.validate()

        spot_state = _api_post("spotClearinghouseState", address)
        user_state = _api_post("clearinghouseState", address)

        # ── Spot Balance ──
        spot_balances = [
            b for b in spot_state.get("balances", [])
            if b["coin"] in DISPLAY_COINS and float(b["total"]) > 0
        ]

        # ── Total available balance (Spot USDC/USDH + Perps) ──
        spot_total = sum(float(b["total"]) for b in spot_balances)
        perps_value = 0.0
        margin_used = 0.0

        if 'marginSummary' in user_state:
            margin_summary = user_state['marginSummary']
            perps_value = float(margin_summary.get('accountValue', 0))
            margin_used = float(margin_summary.get('totalMarginUsed', 0))

        # ── Collect all positions (standard + HIP-3 DEXes) ──
        all_positions = _collect_positions(user_state)

        for dex in KNOWN_HIP3_DEXES:
            try:
                dex_state = _api_post("clearinghouseState", address, dex=dex)
                dex_positions = _collect_positions(dex_state, prefix=dex)
                all_positions.extend(dex_positions)

                if 'marginSummary' in dex_state:
                    dex_margin = dex_state['marginSummary']
                    perps_value += float(dex_margin.get('accountValue', 0))
                    margin_used += float(dex_margin.get('totalMarginUsed', 0))
            except Exception:
                pass

        position_value = sum(p['position_value'] for p in all_positions)
        total_value = spot_total + perps_value

        print("=" * 50)
        print("🏦 HYPERLIQUID ACCOUNT BALANCE")
        print("=" * 50)
        print(f"💰 Total Balance:    ${total_value:,.2f}")
        print(f"   📦 Spot (USDC/USDH):")
        if spot_balances:
            for b in spot_balances:
                print(f"      {b['coin']:6}  ${float(b['total']):,.2f}")
        else:
            print(f"      (none)")
        print(f"   📊 Perps:           ${perps_value:,.2f}")
        print(f"✅ Available:        ${total_value - margin_used:,.2f}")
        print(f"🔒 Margin Used:      ${margin_used:,.2f}")
        print(f"📈 Position Value:   ${position_value:,.2f}")

        if position_value > 0 and total_value > 0:
            leverage = position_value / total_value
            print(f"⚖️  Current Leverage: {leverage:.2f}x")

        print("\n" + "=" * 50)
        print("📋 POSITIONS")
        print("=" * 50)

        if all_positions:
            total_pnl = 0

            for p in all_positions:
                total_pnl += p['unrealized_pnl']
                side = "LONG" if p['size'] > 0 else "SHORT"
                pnl_color = "🟢" if p['unrealized_pnl'] >= 0 else "🔴"

                print(f"{p['coin']:12} | {side:5} | Size: {abs(p['size']):8.4f} | Entry: ${p['entry_px']:8.2f} | PnL: {pnl_color}${p['unrealized_pnl']:8.2f}")

            print("-" * 50)
            total_pnl_color = "🟢" if total_pnl >= 0 else "🔴"
            print(f"{'TOTAL':12} | {'':5} | {'':19} | {'':15} | PnL: {total_pnl_color}${total_pnl:8.2f}")
        else:
            print("No open positions")

        print("=" * 50)

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
