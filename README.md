# Hyperliquid Trading Bot

**English** | [日本語](README_ja.md)

Automated trading bot for Hyperliquid DEX with **HIP-3 multi-DEX support** (trade.xyz, Felix, Markets by Kinetiq, Based, and more).

## ⚠️ Important Disclaimer

**This software is for educational and informational purposes only.**

The author assumes no responsibility for any financial losses resulting from the use of this software. Cryptocurrency trading involves significant risks. Before engaging in actual trading, please ensure the following:

- Understand and thoroughly test the code
- Verify operation with small amounts or on testnet
- Use at your own risk
- Consult with experts before making investment decisions

Please refer to the [LICENSE](./LICENSE) file for detailed disclaimer.

---

## 📋 Table of Contents

- [Setup](#setup)
- [Usage](#usage)
  - [Docker Usage (Recommended)](#-docker-usage-recommended)
  - [Python Usage](#-python-usage)
- [HIP-3 Multi-DEX Trading](#hip-3-multi-dex-trading)
- [Trading Strategies](#trading-strategies)
- [Risk Guardrails](#risk-guardrails)
- [Features](#features)
- [Technical Documentation](#technical-documentation)
- [File Structure](#file-structure)

## Setup

### Create Environment File

```bash
cp .env.example .env
```

### API Key Configuration

Edit the `.env` file and configure the following information:

**Configuration Items:**
- `HYPERLIQUID_ACCOUNT_ADDRESS`: Your main wallet address (the address that holds your funds)
- `HYPERLIQUID_PRIVATE_KEY`: Private key for signing transactions
- `USE_TESTNET`: Set to `true` to use testnet

### Method 1: Direct Private Key Usage
Set your wallet's private key directly.

### Method 2: API Wallet Usage (Recommended)
For a more secure approach, visit [https://app.hyperliquid.xyz/API](https://app.hyperliquid.xyz/API) to generate an API wallet.

Set `HYPERLIQUID_ACCOUNT_ADDRESS` to your main wallet address and `HYPERLIQUID_PRIVATE_KEY` to the API wallet's private key. The API wallet is used only for signing transactions — no fund transfer to the API wallet is required.

## Usage

### 🐳 Docker Usage (Recommended)

#### Prerequisites
```bash
# Create environment file
cp .env.example .env
# Edit .env file to set API keys
```

#### Basic Usage
```bash
# Use latest stable version
docker run --env-file .env ghcr.io/keitaj/hyperliquid-bot:latest

# Run with specific strategy and parameters
docker run --env-file .env ghcr.io/keitaj/hyperliquid-bot:latest \
  python3 bot.py --strategy rsi --rsi-period 21 --oversold-threshold 25

# Run with specific trading coins
docker run --env-file .env ghcr.io/keitaj/hyperliquid-bot:latest \
  python3 bot.py --strategy macd --coins BTC ETH

# Daemon execution (continuous background operation)
docker run -d --name hyperliquid-bot --env-file .env \
  -v $(pwd)/logs:/app/logs ghcr.io/keitaj/hyperliquid-bot:latest
docker logs -f hyperliquid-bot

# Check balance
docker run --rm --env-file .env ghcr.io/keitaj/hyperliquid-bot:latest \
  python3 check_balance.py
```

#### Available Image Tags
- `latest` - Latest stable version
- `v0.1.0` - Specific version

### 🐍 Python Usage

#### Install Dependencies
```bash
pip3 install -r requirements.txt
```

#### Basic Usage
```bash
# Start with default strategy (Simple MA)
python3 bot.py

# Start with specific strategy
python3 bot.py --strategy rsi

# Specify trading coins
python3 bot.py --strategy macd --coins BTC ETH

# Show help
python3 bot.py --help
```

#### Parameter Customization

**Common Parameters**
```bash
# Change position size and profit/loss settings
python3 bot.py --position-size-usd 200 --take-profit-percent 10 --stop-loss-percent 3
```

**Strategy-Specific Parameters**
```bash
# Simple MA Strategy
python3 bot.py --strategy simple_ma --fast-ma-period 5 --slow-ma-period 20

# RSI Strategy
python3 bot.py --strategy rsi --rsi-period 21 --oversold-threshold 25 --overbought-threshold 75

# Bollinger Bands Strategy
python3 bot.py --strategy bollinger_bands --bb-period 25 --std-dev 2.5

# MACD Strategy
python3 bot.py --strategy macd --fast-ema 10 --slow-ema 20 --signal-ema 7

# Grid Trading Strategy
python3 bot.py --strategy grid_trading --grid-levels 15 --grid-spacing-pct 0.3 --position-size-per-grid 30

# Breakout Strategy
python3 bot.py --strategy breakout --lookback-period 30 --volume-multiplier 2.0 --atr-period 20

# Market Making Strategy
python3 bot.py --strategy market_making --spread-bps 10 --order-size-usd 100 --maker-only
```

**Risk Guardrail Parameters**
```bash
# Configure risk limits
python3 bot.py --strategy rsi \
  --max-position-pct 0.1 \
  --max-margin-usage 0.7 \
  --daily-loss-limit 500 \
  --per-trade-stop-loss 0.05 \
  --max-open-positions 3 \
  --risk-level yellow
```

#### Balance & Position Check
```bash
python3 check_balance.py
```

Example output:
```
==================================================
🏦 HYPERLIQUID ACCOUNT BALANCE
==================================================
💰 Total Balance:    $1,299.00
   📦 Spot (USDC/USDH):
      USDC    $1,000.00
      USDH    $0.00
   📊 Perps:           $299.00
✅ Available:        $1,149.00
🔒 Margin Used:      $150.00
📈 Position Value:   $500.00
⚖️  Current Leverage: 0.38x

==================================================
📋 POSITIONS
==================================================
BTC          | LONG  | Size:   0.0050 | Entry: $100000.00 | PnL: 🟢$   5.00
xyz:AAPL     | SHORT | Size:   1.0000 | Entry: $  250.00 | PnL: 🔴$  -2.50
--------------------------------------------------
TOTAL        |       |                |                 | PnL: 🟢$   2.50
==================================================
```

---

## HIP-3 Multi-DEX Trading

[HIP-3](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals) is Hyperliquid's standard for builder-deployed perpetuals DEXes. All HIP-3 DEXes share the same underlying Hyperliquid L1 infrastructure and API, differing only in their listed assets and oracle configurations.

### Supported Platforms

| Platform | DEX Name | Asset Types |
|---|---|---|
| Standard Hyperliquid | (none) | Crypto perps (BTC, ETH, SOL...) |
| [trade.xyz](https://trade.xyz) | `xyz` | Equity & commodity perps (AAPL, GOLD, CL...) |
| [Felix](https://trade.usefelix.xyz) | `flx` | Equity & commodity perps |
| [Markets by Kinetiq](https://markets.xyz) | `km` | Various (collateral: USDH) |
| [Based](https://basedapp.xyz) | (none) | Standard HL frontend — use `ENABLE_STANDARD_HL=true` |
| [Ventuals](https://app.ventuals.com) | `vntl` | — |
| [HyENA](https://app.hyena.trade) | `hyna` | — |
| [dreamcash](https://trade.dreamcash.xyz) | `cash` | — |

> **Note**: DEX names are assigned on-chain. Run `{"type": "perpDexs"}` against the Hyperliquid API to see the current full list.

### Configuration

Add the following to your `.env` file:

```bash
# Comma-separated HIP-3 DEX names to trade on
TRADING_DEXES=xyz,flx

# Set to false to trade only on HIP-3 DEXes (disable standard HL perps)
ENABLE_STANDARD_HL=true

# Per-DEX coin lists (optional — defaults to all available coins on that DEX)
XYZ_COINS=XYZ100,XYZ200
FLX_COINS=NVDA,AAPL,WTI
```

### HIP-3 Command-Line Options

```bash
# Trade only on trade.xyz with RSI strategy
python3 bot.py --strategy rsi --dex xyz --no-hl

# Trade Felix equity perps (NVDA, AAPL) + standard HL BTC/ETH simultaneously
FLX_COINS=NVDA,AAPL python3 bot.py --strategy simple_ma --coins BTC ETH --dex flx

# Trade across all configured DEXes (set TRADING_DEXES in .env)
python3 bot.py --strategy macd
```

| Flag | Description |
|---|---|
| `--dex DEX [DEX ...]` | HIP-3 DEX names to trade (overrides `TRADING_DEXES` env var) |
| `--no-hl` | Disable standard Hyperliquid perps, trade only HIP-3 DEXes |

### How HIP-3 Works Internally

HIP-3 assets use a special integer asset ID scheme:

```
asset_id = 100000 + (perp_dex_index × 10000) + index_in_meta
```

For example, if `xyz` is the 2nd DEX (index 1) and `XYZ100` is its first asset (index 0):
```
asset_id = 100000 + (1 × 10000) + 0 = 110000
```

The bot handles this automatically at startup:
1. Calls `perpDexs` API to discover all registered DEXes and their indices
2. Calls `meta` for each configured DEX to get its asset list
3. Computes asset IDs and injects them into the SDK's lookup table
4. Represents HIP-3 coins as `"dex:coin"` strings (e.g. `"xyz:XYZ100"`, `"flx:NVDA"`)

---

## Features

- **Market Data**: Real-time price, order book, and candlestick data retrieval
- **Order Management**: Limit and market order placement and cancellation
- **Risk Management**: Leverage limits, maximum drawdown, daily loss limits
- **Multiple Strategies**: Choose from 7 different trading strategies
- **Risk Guardrails**: Configurable margin limits, daily loss limits, per-trade stop loss, and dynamic risk levels
- **HIP-3 Multi-DEX**: Trade across Hyperliquid, trade.xyz, Felix, and other HIP-3 DEXes simultaneously

## Trading Strategies

### 1. Simple MA Strategy (`simple_ma`)
- Short-term and long-term moving average crossover
- Buy on golden cross, sell on death cross
- Default parameters: `fast_ma_period=10`, `slow_ma_period=30`

**Command-line Parameters:**
- `--fast-ma-period`: Fast moving average period (default: 10)
- `--slow-ma-period`: Slow moving average period (default: 30)

### 2. RSI Strategy (`rsi`)
- Relative Strength Index for overbought/oversold conditions
- Buy when RSI < 30, sell when RSI > 70
- Default parameters: `rsi_period=14`, `oversold=30`, `overbought=70`

**Command-line Parameters:**
- `--rsi-period`: RSI calculation period (default: 14)
- `--oversold-threshold`: Oversold threshold (default: 30)
- `--overbought-threshold`: Overbought threshold (default: 70)

### 3. Bollinger Bands Strategy (`bollinger_bands`)
- Uses price deviation from Bollinger Bands
- Buy on lower band touch, sell on upper band touch
- Breakout detection during volatility expansion
- Default parameters: `bb_period=20`, `std_dev=2`, `squeeze_threshold=0.02`

**Command-line Parameters:**
- `--bb-period`: Bollinger Bands calculation period (default: 20)
- `--std-dev`: Standard deviation multiplier (default: 2)
- `--squeeze-threshold`: Squeeze detection threshold (default: 0.02)

### 4. MACD Strategy (`macd`)
- MACD line and signal line crossover
- Divergence detection capability
- Default parameters: `fast_ema=12`, `slow_ema=26`, `signal_ema=9`

**Command-line Parameters:**
- `--fast-ema`: Fast EMA period (default: 12)
- `--slow-ema`: Slow EMA period (default: 26)
- `--signal-ema`: Signal line EMA period (default: 9)

### 5. Grid Trading Strategy (`grid_trading`)
- Places buy and sell orders at regular intervals in ranging markets
- Accumulates profits as price moves up and down
- Default parameters: `grid_levels=10`, `grid_spacing_pct=0.5%`, `range_period=100`

**Command-line Parameters:**
- `--grid-levels`: Number of grid levels (default: 10)
- `--grid-spacing-pct`: Grid spacing percentage (default: 0.5)
- `--position-size-per-grid`: Position size per grid (default: 50)
- `--range-period`: Range calculation period (default: 100)

### 6. Breakout Strategy (`breakout`)
- Detects support and resistance line breakouts
- Volume confirmation and ATR-based stop loss management
- Default parameters: `lookback_period=20`, `volume_multiplier=1.5`, `atr_period=14`

**Command-line Parameters:**
- `--lookback-period`: Support/resistance calculation period (default: 20)
- `--volume-multiplier`: Volume confirmation multiplier (default: 1.5)
- `--breakout-confirmation-bars`: Bars required for breakout confirmation (default: 2)
- `--atr-period`: ATR calculation period (default: 14)

### 7. Market Making Strategy (`market_making`)
- Places symmetric buy/sell limit orders around the mid price to capture bid-ask spreads
- Automatically refreshes stale orders and manages position risk
- Supports maker-only (post-only) mode for guaranteed maker rebates
- Default parameters: `spread_bps=5`, `order_size_usd=50`, `max_open_orders=4`, `refresh_interval=30s`, `max_position_age=120s`

**Command-line Parameters:**
- `--spread-bps`: Spread from mid price in basis points (default: 5)
- `--order-size-usd`: Size per order in USD (default: 50)
- `--max-open-orders`: Maximum concurrent open orders (default: 4)
- `--refresh-interval`: Seconds before cancelling stale orders (default: 30)
- `--no-close-immediately`: Disable immediate position closing (use take-profit limits instead)
- `--max-position-age`: Maximum seconds to hold a position before force-close (default: 120)
- `--maker-only`: Use post-only (maker) orders for all trades

## Risk Guardrails

Configurable risk management parameters via environment variables or CLI flags. CLI flags take precedence over environment variables.

| Parameter | Env Var | CLI Flag | Default | Description |
|---|---|---|---|---|
| Max Position % | `MAX_POSITION_PCT` | `--max-position-pct` | 0.2 | Max single position as % of account |
| Max Margin Usage | `MAX_MARGIN_USAGE` | `--max-margin-usage` | 0.8 | Stop new orders above this margin ratio |
| Force Close Margin | `FORCE_CLOSE_MARGIN` | `--force-close-margin` | — | Force close ALL positions above this ratio |
| Daily Loss Limit | `DAILY_LOSS_LIMIT` | `--daily-loss-limit` | — | Absolute $ daily loss to auto-stop bot |
| Per-Trade Stop Loss | `PER_TRADE_STOP_LOSS` | `--per-trade-stop-loss` | — | Cut losing trades at this % loss (e.g., 0.05 = 5%) |
| Max Open Positions | `MAX_OPEN_POSITIONS` | `--max-open-positions` | 5 | Max concurrent open positions |
| Cooldown After Stop | `COOLDOWN_AFTER_STOP` | `--cooldown-after-stop` | 3600 | Seconds to wait after emergency stop |
| Risk Level | `RISK_LEVEL` | `--risk-level` | green | `green` (100%), `yellow` (50%), `red` (pause), `black` (close all) |

## Technical Documentation

For more detailed technical information, please refer to the following documents:

- [Timeframes and Parameters Details](./docs/technical-notes/timeframes.md) - Explanation of timeframes and parameter units for each strategy
- [Docker Release Process](./docs/docker-release.md) - About automatic Docker image releases

## File Structure

- `bot.py`: Main bot class
- `config.py`: Configuration management
- `market_data.py`: Market data retrieval
- `order_manager.py`: Order management
- `risk_manager.py`: Risk management
- `rate_limiter.py`: API rate limiting
- `hip3/`: HIP-3 multi-DEX support
  - `dex_registry.py`: DEX discovery and asset ID resolution
  - `multi_dex_market_data.py`: DEX-aware market data
  - `multi_dex_order_manager.py`: DEX-aware order management
- `strategies/`: Trading strategies
  - `base_strategy.py`: Base strategy class
  - `simple_ma_strategy.py`: Moving average strategy
  - `rsi_strategy.py`: RSI strategy
  - `bollinger_bands_strategy.py`: Bollinger Bands strategy
  - `macd_strategy.py`: MACD strategy
  - `grid_trading_strategy.py`: Grid trading strategy
  - `breakout_strategy.py`: Breakout strategy
  - `market_making_strategy.py`: Market making strategy
- `validation/`: Pre-trade validation
  - `margin_validator.py`: Margin and configuration validation
- `docs/`: Documentation
  - `technical-notes/`: Technical detail documents

## Notes

- Before using in production, always test on testnet first
- Keep your private keys secure
- Set risk management parameters carefully
- HIP-3 DEXes may charge higher fees than standard Hyperliquid (typically 2x, with 50% going to the DEX deployer)
- HIP-3 DEXes currently support isolated margin only (cross-margin not available)
