#!/usr/bin/env python3
"""
Test script to verify Hyperliquid API connection and fetch market data
No authentication required for public endpoints
"""

import json
from hyperliquid.info import Info
from hyperliquid.utils import constants

def test_market_data():
    print("Testing Hyperliquid Market Data API...")
    print("=" * 50)
    
    # Connect to testnet (no auth required for public data)
    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    
    # Test 1: Get all mid prices
    print("\n1. Fetching mid prices for all coins...")
    try:
        all_mids = info.all_mids()
        print(f"Found {len(all_mids)} coins")
        # Show first 5 coins
        for i, (coin, price) in enumerate(list(all_mids.items())[:5]):
            print(f"  {coin}: ${price}")
        print("  ...")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 2: Get BTC order book
    print("\n2. Fetching BTC order book...")
    try:
        l2_data = info.l2_snapshot("BTC")
        if l2_data and 'levels' in l2_data:
            bids = l2_data['levels'][0][:3]  # Top 3 bids
            asks = l2_data['levels'][1][:3]  # Top 3 asks
            
            print("  Top 3 Bids:")
            for bid in bids:
                print(f"    Price: ${bid['px']}, Size: {bid['sz']}")
            
            print("  Top 3 Asks:")
            for ask in asks:
                print(f"    Price: ${ask['px']}, Size: {ask['sz']}")
                
            if bids and asks:
                spread = float(asks[0]['px']) - float(bids[0]['px'])
                print(f"  Spread: ${spread:.2f}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 3: Get meta info
    print("\n3. Fetching exchange meta info...")
    try:
        meta = info.meta()
        print(f"  Number of assets: {len(meta['universe'])}")
        # Show first asset info
        if meta['universe']:
            asset = meta['universe'][0]
            print(f"  Example asset: {asset['name']}")
            print(f"    Size decimals: {asset['szDecimals']}")
            print(f"    Max leverage: {asset['maxLeverage']}")
    except Exception as e:
        print(f"Error: {e}")
    
    print("\n" + "=" * 50)
    print("Market data test completed!")
    print("\nTo use the trading bot:")
    print("1. Add your wallet credentials to .env file")
    print("2. Fund your testnet account at https://app.hyperliquid.xyz/faucet")
    print("3. Run 'python3 bot.py'")

if __name__ == "__main__":
    test_market_data()