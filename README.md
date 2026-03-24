# Hyperliquid Trading Bot

[![Test](https://github.com/keitaj/hyperliquid-bot/actions/workflows/test.yml/badge.svg)](https://github.com/keitaj/hyperliquid-bot/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Release](https://img.shields.io/github/v/release/keitaj/hyperliquid-bot)](https://github.com/keitaj/hyperliquid-bot/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

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
- [Parameter Reference (for AI agents)](#parameter-reference-for-ai-agents)

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
- `v0.3.0` - Specific version

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
python3 bot.py --strategy market_making --spread-bps 10 --order-size-usd 100 --maker-only --taker-fallback-age 60
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
📦 Spot (USDC/USDH):
   USDC    $1,000.00
   USDH    $0.00
📊 Perps:           $299.00
📈 Position Value:   $500.00

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

| # | Strategy | Description |
|---|---|---|
| 1 | `simple_ma` | Moving average crossover — buy on golden cross, sell on death cross |
| 2 | `rsi` | RSI overbought/oversold — buy when RSI < 30, sell when RSI > 70 |
| 3 | `bollinger_bands` | Bollinger Bands bounce and volatility breakout |
| 4 | `macd` | MACD/signal crossover with divergence detection |
| 5 | `grid_trading` | Grid orders at regular intervals in ranging markets |
| 6 | `breakout` | Support/resistance breakout with volume and ATR confirmation |
| 7 | `market_making` | Symmetric buy/sell limits around mid price for spread capture |

All parameters are configurable via CLI flags with sensible defaults.
Run `python3 bot.py --help` for the full list, or see [Parameter Reference](#parameter-reference-for-ai-agents) below.

## Risk Guardrails

Configurable risk management via environment variables or CLI flags (CLI takes precedence).

| Env Var | CLI Flag | Default | Description |
|---|---|---|---|
| `MAX_POSITION_PCT` | `--max-position-pct` | 0.2 | Max single position as % of account |
| `MAX_MARGIN_USAGE` | `--max-margin-usage` | 0.8 | Stop new orders above this margin ratio |
| `FORCE_CLOSE_MARGIN` | `--force-close-margin` | — | Force close ALL positions above this ratio |
| `DAILY_LOSS_LIMIT` | `--daily-loss-limit` | — | Absolute $ daily loss to auto-stop bot |
| `PER_TRADE_STOP_LOSS` | `--per-trade-stop-loss` | — | Cut losing trades at this % (e.g., 0.05 = 5%) |
| `MAX_OPEN_POSITIONS` | `--max-open-positions` | 5 | Max concurrent open positions |
| `COOLDOWN_AFTER_STOP` | `--cooldown-after-stop` | 3600 | Seconds to wait after emergency stop |
| `RISK_LEVEL` | `--risk-level` | green | `green` (100%), `yellow` (50%), `red` (pause), `black` (close all) |
| `METRICS_CACHE_TTL` | — | 2.0 | Seconds to cache risk metrics before re-fetching (recommend 10+ for 6+ coins) |

### Rate Limiter

Hyperliquid allows 1,200 weight/minute (~20 req/sec). The rate limiter is configurable via environment variables:

| Env Var | Default | Description |
|---|---|---|
| `RATE_LIMIT_RPS` | 5.0 | Requests per second (max 20) |
| `RATE_LIMIT_BURST` | 8 | Burst limit (max 20) |
| `RATE_LIMIT_BACKOFF` | 2.0 | Backoff multiplier on rate limit errors |
| `RATE_LIMIT_MAX_BACKOFF` | 30.0 | Maximum backoff seconds |

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

---

## Parameter Reference (for AI agents)

> This section is formatted for machine consumption. All CLI flags, config keys, and default values per strategy are listed below as structured YAML. CLI flags use `--kebab-case`, config dict keys use `snake_case`. Config merging: `default_configs[strategy]` is the base; CLI overrides are merged on top.

```yaml
system:
  main_loop_interval: 10          # --main-loop-interval  (seconds)
  market_order_slippage: 0.01     # --market-order-slippage  (0.01 = 1%)

strategies:
  simple_ma:
    candle_interval: "5m"         # --candle-interval
    fast_ma_period: 10            # --fast-ma-period
    slow_ma_period: 30            # --slow-ma-period
    position_size_usd: 100        # --position-size-usd
    max_positions: 3              # --max-positions
    take_profit_percent: 5        # --take-profit-percent
    stop_loss_percent: 2          # --stop-loss-percent

  rsi:
    candle_interval: "15m"
    rsi_period: 14                # --rsi-period
    oversold_threshold: 30        # --oversold-threshold
    overbought_threshold: 70      # --overbought-threshold
    rsi_extreme_low: 25           # --rsi-extreme-low  (RSI below this → size × extreme multiplier)
    rsi_moderate_low: 35          # --rsi-moderate-low  (RSI below this → size × moderate multiplier)
    size_multiplier_extreme: 1.5  # --size-multiplier-extreme
    size_multiplier_moderate: 1.2 # --size-multiplier-moderate
    position_size_usd: 100
    max_positions: 3
    take_profit_percent: 5
    stop_loss_percent: 2

  bollinger_bands:
    candle_interval: "15m"
    bb_period: 20                         # --bb-period
    std_dev: 2                            # --std-dev
    squeeze_threshold: 0.02               # --squeeze-threshold
    volatility_expansion_threshold: 1.5   # --volatility-expansion-threshold
    high_band_width_threshold: 0.05       # --high-band-width-threshold  (band_width > this → size × high multiplier)
    high_band_width_multiplier: 0.8       # --high-band-width-multiplier
    low_band_width_threshold: 0.02        # --low-band-width-threshold  (band_width < this → size × low multiplier)
    low_band_width_multiplier: 1.2        # --low-band-width-multiplier
    position_size_usd: 100
    max_positions: 3
    take_profit_percent: 5
    stop_loss_percent: 2

  macd:
    candle_interval: "15m"
    fast_ema: 12                  # --fast-ema
    slow_ema: 26                  # --slow-ema
    signal_ema: 9                 # --signal-ema
    divergence_lookback: 20       # --divergence-lookback
    histogram_strength_high: 0.5  # --histogram-strength-high  (histogram% > this → size × high multiplier)
    histogram_strength_low: 0.1   # --histogram-strength-low  (histogram% < this → size × low multiplier)
    histogram_multiplier_high: 1.3 # --histogram-multiplier-high
    histogram_multiplier_low: 0.7  # --histogram-multiplier-low
    position_size_usd: 100
    max_positions: 3
    take_profit_percent: 5
    stop_loss_percent: 2

  grid_trading:
    candle_interval: "15m"
    grid_levels: 10                   # --grid-levels
    grid_spacing_pct: 0.5             # --grid-spacing-pct
    position_size_per_grid: 50        # --position-size-per-grid
    range_period: 100                 # --range-period
    range_pct_threshold: 10           # --range-pct-threshold  (range% < this → ranging market)
    volatility_threshold: 0.15        # --volatility-threshold  (vol < this → ranging market)
    grid_recalc_bars: 20              # --grid-recalc-bars
    grid_saturation_threshold: 0.7    # --grid-saturation-threshold  (fill ratio > this → size × 0.5)
    grid_boundary_margin_low: 0.98    # --grid-boundary-margin-low
    grid_boundary_margin_high: 1.02   # --grid-boundary-margin-high
    account_cap_pct: 0.05             # --account-cap-pct
    max_positions: 5
    take_profit_percent: 2
    stop_loss_percent: 5

  breakout:
    candle_interval: "15m"
    lookback_period: 20                    # --lookback-period
    volume_multiplier: 1.5                 # --volume-multiplier
    breakout_confirmation_bars: 2          # --breakout-confirmation-bars
    atr_period: 14                         # --atr-period
    pivot_window: 5                        # --pivot-window
    avg_volume_lookback: 20                # --avg-volume-lookback
    stop_loss_atr_multiplier: 1.5          # --stop-loss-atr-multiplier
    position_stop_loss_atr_multiplier: 2.0 # --position-stop-loss-atr-multiplier
    strong_breakout_multiplier: 1.5        # --strong-breakout-multiplier
    high_atr_threshold: 3.0               # --high-atr-threshold  (ATR% > this → size × high multiplier)
    low_atr_threshold: 1.0                # --low-atr-threshold  (ATR% < this → size × low multiplier)
    high_atr_multiplier: 0.7              # --high-atr-multiplier
    low_atr_multiplier: 1.3               # --low-atr-multiplier
    position_size_usd: 100
    max_positions: 3
    take_profit_percent: 7
    stop_loss_percent: 3

  market_making:
    spread_bps: 5                      # --spread-bps
    order_size_usd: 50                 # --order-size-usd
    max_open_orders: 4                 # --max-open-orders
    refresh_interval_seconds: 30       # --refresh-interval
    close_immediately: true            # --no-close-immediately  (flag inverts this)
    max_position_age_seconds: 120      # --max-position-age
    maker_only: false                  # --maker-only
    taker_fallback_age_seconds: null   # --taker-fallback-age  (seconds after max-position-age to fall back to taker; null = never)
    account_cap_pct: 0.05              # --account-cap-pct
    max_positions: 3
    take_profit_percent: 1
    stop_loss_percent: 2

risk_guardrails:
  max_position_pct: 0.2           # --max-position-pct  / env MAX_POSITION_PCT
  max_margin_usage: 0.8           # --max-margin-usage  / env MAX_MARGIN_USAGE
  force_close_margin: null        # --force-close-margin  / env FORCE_CLOSE_MARGIN
  daily_loss_limit: null          # --daily-loss-limit  / env DAILY_LOSS_LIMIT
  per_trade_stop_loss: null       # --per-trade-stop-loss  / env PER_TRADE_STOP_LOSS
  max_open_positions: 5           # --max-open-positions  / env MAX_OPEN_POSITIONS
  cooldown_after_stop: 3600       # --cooldown-after-stop  / env COOLDOWN_AFTER_STOP
  risk_level: "green"             # --risk-level  / env RISK_LEVEL  (green|yellow|red|black)
  metrics_cache_ttl: 2.0          # env METRICS_CACHE_TTL  (seconds; recommend 10+ for 6+ coins)

hip3:
  env:
    TRADING_DEXES: ""             # Comma-separated DEX names (e.g. "xyz,flx")
    ENABLE_STANDARD_HL: "true"    # Trade standard HL perps alongside HIP-3
    "{DEX}_COINS": ""             # Per-DEX coin list (e.g. XYZ_COINS=XYZ100,XYZ200)
  cli:
    --dex: []                     # HIP-3 DEX names (overrides TRADING_DEXES)
    --no-hl: false                # Disable standard HL perps

config_merge_order: "default_configs[strategy] ← CLI overrides (only non-null)"
priority: "CLI flag > env var > default_configs > strategy constructor fallback"
```
