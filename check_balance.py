#!/usr/bin/env python3
"""
Simple script to check Hyperliquid account balance and positions
"""

from bot import HyperliquidBot
import sys

def main():
    try:
        bot = HyperliquidBot()
        user_state = bot.get_user_state()

        if user_state and 'marginSummary' in user_state:
            margin_summary = user_state['marginSummary']
            
            print("=" * 50)
            print("🏦 HYPERLIQUID ACCOUNT BALANCE")
            print("=" * 50)
            
            account_value = float(margin_summary.get('accountValue', 0))
            margin_used = float(margin_summary.get('totalMarginUsed', 0))
            available = account_value - margin_used
            position_value = float(margin_summary.get('totalNtlPos', 0))
            
            print(f"💰 Account Value:    ${account_value:,.2f}")
            print(f"✅ Available:        ${available:,.2f}")
            print(f"🔒 Margin Used:      ${margin_used:,.2f}")
            print(f"📈 Position Value:   ${position_value:,.2f}")
            
            if position_value > 0:
                leverage = position_value / account_value if account_value > 0 else 0
                print(f"⚖️  Current Leverage: {leverage:.2f}x")
            
            print("\n" + "=" * 50)
            print("📋 POSITIONS")
            print("=" * 50)
            
            if 'assetPositions' in user_state and user_state['assetPositions']:
                positions = user_state['assetPositions']
                total_pnl = 0
                
                for pos in positions:
                    position = pos['position']
                    coin = position['coin']
                    size = float(position['szi'])
                    entry_px = float(position.get('entryPx', 0))
                    unrealized_pnl = float(position.get('unrealizedPnl', 0))
                    total_pnl += unrealized_pnl
                    
                    side = "LONG" if size > 0 else "SHORT"
                    pnl_color = "🟢" if unrealized_pnl >= 0 else "🔴"
                    
                    print(f"{coin:6} | {side:5} | Size: {abs(size):8.4f} | Entry: ${entry_px:8.2f} | PnL: {pnl_color}${unrealized_pnl:8.2f}")
                
                print("-" * 50)
                total_pnl_color = "🟢" if total_pnl >= 0 else "🔴"
                print(f"{'TOTAL':6} | {'':5} | {'':19} | {'':15} | PnL: {total_pnl_color}${total_pnl:8.2f}")
                
            else:
                print("No open positions")
            
            print("=" * 50)
            
        else:
            print("❌ Error: Could not retrieve account balance")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()