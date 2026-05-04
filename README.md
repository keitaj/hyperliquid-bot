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
pip3 install .
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

# Market Making with BBO mode (place orders at best bid/ask)
python3 bot.py --strategy market_making --bbo-mode --bbo-offset-bps 0.5
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

# Per-DEX builder fee (optional — required by some HIP-3 deployers'
# rewards programs).  Setting BUILDER_FEES_<DEX>_ADDRESS attaches the
# given builder code to every order on that DEX and pre-approves it on
# bot startup.  Leave unset to keep the previous no-builder behaviour.
# BUILDER_FEES_<DEX>_TENTHS_BPS sets the per-order builder fee in tenths
# of a basis point (10 = 1 bp = 0.01% of order notional, default 10).
# BUILDER_FEES_<DEX>_MAX_FEE_RATE is the pre-approval cap and must be
# >= the per-order fee (otherwise the exchange rejects every order).
# Default is 0.05%, which leaves headroom up to f=50.
# Example for a DEX whose deployer publishes a builder code:
# BUILDER_FEES_CASH_ADDRESS=0xabc...
# BUILDER_FEES_CASH_TENTHS_BPS=10        # 1 bp per order
# BUILDER_FEES_CASH_MAX_FEE_RATE=0.05%   # cap at 5 bp
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
| `--enable-ws` | Enable WebSocket feed for real-time L2 book updates (reduces REST API calls) |

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
- **Startup Validation**: Strategy parameters validated at startup with all errors reported at once
- **Structured Logging**: JSON output mode for log aggregation tools (`LOG_FORMAT=json`)
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

### JSON config layer

Both `--config <path.json>` and `$BOT_CONFIG=<path.json>` (env-var fallback) are accepted. Repeat `--config` to layer multiple files (later files override earlier). Layering precedence is **CLI > env > JSON > dataclass defaults** — JSON simply slots in as a more readable surface format on top of the existing flat key namespace.

JSON files may use either flat or nested form (or mix). Nested form is auto-flattened by underscore-joining the path under known namespaces (`market_making`, `risk`):

```json
{
  "market_making": {
    "spread_bps": 10,
    "refresh": { "tolerance_bp": 1, "max_age_seconds": 240 },
    "forager": { "enabled": true, "score_threshold": 30.0 }
  },
  "risk": { "daily_loss_limit": 200 }
}
```

becomes:

```python
{"spread_bps": 10, "refresh_tolerance_bp": 1, "refresh_max_age_seconds": 240,
 "forager_enabled": True, "forager_score_threshold": 30.0, "daily_loss_limit": 200}
```

Unknown keys produce a warning (typo detection) but are still passed through to the validator, which decides whether to abort. Missing files are warned and skipped (so a typo in `--config /missing.json` does not block startup); malformed JSON aborts with exit code 2. See `examples/config.example.json` for a full template.

The `risk` namespace flows to the bot's `Config` class (the same path the existing `--max-position-pct` / `--daily-loss-limit` / etc. CLI flags target), not to `strategy_config`. CLI flags still beat JSON for any individual risk parameter, so an operator can leave defaults in JSON and override per-run from the command line.

The `market_making` strategy uses **progressive close pricing**: as a position ages, the take-profit price is tightened from full spread → breakeven (at 50% of max age) → small loss (at 75%), reducing costly taker force-closes. The loss tolerance is configurable via `--aggressive-loss-bps` (default: 1 bps). During the force-close phase, `--force-close-max-loss-bps` enables progressive loss acceptance that scales from `aggressive-loss-bps` to the configured maximum as the position approaches the taker deadline. **Unrealized loss early close** (`--unrealized-loss-close-bps`): When a position's unrealized loss exceeds this threshold (in bps), it is immediately closed via taker order regardless of position age. This caps large adverse moves before the age-based close triggers. Default: 0 (disabled).

**BBO mode** (`--bbo-mode`): Places orders at the best bid/ask instead of `mid ± spread_bps`. On Hyperliquid, market spreads are typically 0.1–2 bps, so even `SPREAD_BPS=5` places orders 4–5 bps away from BBO, resulting in low fill rates. BBO mode improves fill rates by tracking the current best prices. Use `--bbo-offset-bps N` to place orders N bps behind BBO (default: 0 = at BBO). Falls back to `mid ± spread_bps` when BBO is unavailable.

**Refresh tolerance** (`--refresh-tolerance-bp`): Keep an existing order across cycles when its recorded price drifted no more than this many basis points from the current ideal price. Reduces unnecessary cancel/replace traffic and preserves queue priority on Hyperliquid's price–time matching when the market is quiet. The cancel still fires immediately when the drift exceeds tolerance. A safety-net upper bound on order age applies independently via `--refresh-max-age-seconds` (default: `refresh_interval_seconds * 4`). Default: `0` (disabled, age-only behaviour preserved for full backward compatibility).

**Per-coin overrides** (`--coin-offset-overrides`, `--coin-spread-overrides`, `--coin-size-overrides`, `--coin-unrealized-loss-overrides`): Override BBO offset, spread, order size, or the unrealized-loss early-close threshold per coin. Format: `"SP500:0.5,MSFT:3"`. Supports both bare names and DEX-prefixed names (`xyz:SP500:0.5`). Unspecified coins use the global default. Use `--coin-size-overrides` to set per-coin order size in USD (e.g., `"TSLA:150,NVDA:150"`); setting a coin to `0` skips orders for that coin. Use `--coin-unrealized-loss-overrides` to relax the threshold on low-vol coins (`"INTC:25"`) or tighten it on volatile ones (`"OIL:10"`); setting a coin to `0` disables the unrealized-loss feature for that coin.

**Quiet hours** (`--quiet-hours-utc`): Stop or widen quoting during specific UTC hours (e.g., `"17"` or `"17,18"`). Default: stop quoting entirely. With `--quiet-hours-spread-multiplier N`, widens spread by Nx instead. Positions are still managed during quiet hours.

**Drain mode** (`--drain-flag-file`): Path to a flag file used for graceful pre-shutdown. When the file exists, the strategy stops placing new entry orders and only manages existing positions via the normal maker-first close flow. Designed to be triggered by an external script (e.g., a session-switch helper) before sending SIGTERM, so positions can unwind via maker close instead of taker IOC close. Drain takes priority over quiet hours when both apply. Empty/unset = disabled.

**Spread schedule** (`--spread-schedule`): Per-hour spread multiplier for time-of-day spread control. Format: `"HOUR:MULT,..."` or `"START-END:MULT,..."` for ranges (e.g., `"0-3:1.5,14:1.5,20:1.5"`). Ranges wrap around midnight (`"22-2:1.5"` covers hours 22,23,0,1,2). Hours not in the schedule use multiplier 1.0 (no change). Multiplier 0 triggers full-stop mode (same as quiet hours). Coexists with quiet hours — quiet hours full-stop takes priority; otherwise multipliers stack.

**Auto-exclude on adverse selection** (`--auto-exclude`): Automatically pauses a coin when the AdverseSelectionTracker reports moderate adverse selection (`avg_<window>` below `--auto-exclude-threshold-bps`, default `-3.0`) for `--auto-exclude-consecutive` summary windows in a row (default 3, ~15 min with the default 300s log interval). The coin is paused for `--auto-exclude-cooldown` seconds (default 1800) and then automatically resumes. Requires `--enable-adverse-selection-log`. Per-window `min_fills` filtering keeps low-volume noise from triggering. Shares the per-coin cooldown map with `--loss-streak-limit`, so the two features compose naturally.

**Forager: composite-score auto-exclude** (`--forager`): Complements `--auto-exclude` (markout-based) by scoring each coin on three independent dimensions and pausing it when the composite stays low. The dimensions are **activity** (recency of fills, catches dead markets like a coin that hasn't filled in hours), **close quality** (maker close rate, catches coins with structural taker fallback even when markout looks neutral), and **cost** (recent $/1K vol, catches gradual bleed). A weighted composite in `[0, 100]` is computed each cycle; below `--forager-threshold` (default 30) for `--forager-consecutive` checks (default 3) triggers `--forager-cooldown` seconds (default 1800) on the shared cooldown map. Weights are configurable via `--forager-w-activity`, `--forager-w-quality`, `--forager-w-cost`. Internal formula constants (window length, idle grace, cost scale, min-closes gate) can be overridden via env vars (`FORAGER_WINDOW_SECONDS`, `FORAGER_ACTIVITY_IDLE_MIN_SECONDS`, `FORAGER_COST_MAX_PER_1K`, `FORAGER_MIN_CLOSES_FOR_QUALITY`, `FORAGER_CHECK_INTERVAL_SECONDS`). Default disabled; both Forager and `--auto-exclude` may run side-by-side and either may set the cooldown.

**WebSocket guards** (require `--enable-ws`):
- `--bbo-guard-threshold-bps`: Cancel stale entry orders when BBO moves (default: 2.0)
- `--imbalance-guard-threshold`: Cancel one side when L2 book is skewed (0–1, default: 0)
- `--close-refresh-threshold-bps`: Refresh close orders on BBO change to improve maker fill rate (default: 0 = disabled)

**Adverse selection logging** (`--enable-adverse-selection-log`): Measures mid-price movement 5s/30s/60s after each fill, logging per-coin summaries every 300s. Observation only — no trading impact.

**Dynamic offset** (`--dynamic-offset`): Auto-adjusts per-coin BBO offset based on adverse selection severity from the tracker. Coins with higher adverse selection get wider offsets; favorable coins get tighter offsets. Requires `--enable-ws` and `--enable-adverse-selection-log`. Manual `--coin-offset-overrides` serve as the baseline; dynamic adjustment adds/subtracts from it.

**Dynamic position age** (`--dynamic-age`): Adjusts `MAX_POSITION_AGE` per coin based on recent volatility. High-volatility coins get shorter holding times (reducing adverse selection risk), while low-volatility coins get longer times (improving maker fill probability). Uses the same mid-price history as `--vol-adjust`. Configure `--dynamic-age-baseline-vol` (bps) as the "normal" volatility reference, and `--dynamic-age-min` / `--dynamic-age-max` (seconds) for clamping bounds. Falls back to the fixed `--max-position-age` when data is insufficient.

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
| `META_CACHE_TTL` | — | 3600 | Seconds to cache asset metadata (sz_decimals) |
| `MIDS_CACHE_TTL` | — | 5.0 | Seconds to cache mid prices in order manager |

### Margin Validation

Minimum order values and margin multipliers used during startup validation. Override via environment variables if Hyperliquid changes its requirements.

| Env Var | Default | Description |
|---|---|---|
| `MIN_ORDER_VALUE_DEFAULT` | 50 | Default minimum order value in USD |
| `MIN_ORDER_VALUE_BTC` | 100 | Minimum order value for BTC |
| `MIN_ORDER_VALUE_ETH` | 100 | Minimum order value for ETH |
| `MIN_ORDER_VALUE_{COIN}` | — | Minimum order value for any coin (e.g. `MIN_ORDER_VALUE_SOL=80`) |
| `INITIAL_MARGIN_MULTIPLIER` | 3.0 | Margin multiplier for initial orders |
| `MARGIN_SAFETY_BUFFER` | 1.5 | Safety buffer on margin calculations |

### Rate Limiter

Hyperliquid allows 1,200 weight/minute (~20 req/sec). The rate limiter is configurable via environment variables:

| Env Var | Default | Description |
|---|---|---|
| `RATE_LIMIT_RPS` | 5.0 | Requests per second (max 20) |
| `RATE_LIMIT_BURST` | 8 | Burst limit (max 20) |
| `RATE_LIMIT_BACKOFF` | 2.0 | Backoff multiplier on rate limit errors |
| `RATE_LIMIT_MAX_BACKOFF` | 30.0 | Maximum backoff seconds |

### Logging

| Env Var | Default | Description |
|---|---|---|
| `LOG_FORMAT` | `text` | Log output format: `text` (human-readable) or `json` (structured, one JSON object per line) |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

JSON mode is useful for log aggregation tools (Datadog, CloudWatch, Loki, etc.). Example:

```bash
LOG_FORMAT=json LOG_LEVEL=DEBUG python3 bot.py --strategy rsi
```

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
- `log_config.py`: Logging setup (text / JSON structured output)
- `coin_utils.py`: Shared HIP-3 coin notation helpers
- `account_utils.py`: Account balance helpers (Portfolio Margin)
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
  - `mm_order_tracker.py`: MM order tracking and stale order management
  - `mm_position_closer.py`: MM position close and take-profit management
- `validation/`: Pre-trade validation
  - `margin_validator.py`: Margin and configuration validation
  - `strategy_validator.py`: Strategy parameter validation
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
    refresh_tolerance_bp: 0            # --refresh-tolerance-bp  (keep an order across cycles when its price drift <= this many bps; 0 = disabled, age-only)
    refresh_max_age_seconds: null      # --refresh-max-age-seconds  (safety-net upper bound on age of a kept order; null = refresh_interval_seconds * 4)
    close_immediately: true            # --no-close-immediately  (flag inverts this)
    max_position_age_seconds: 120      # --max-position-age
    maker_only: false                  # --maker-only
    taker_fallback_age_seconds: null   # --taker-fallback-age  (seconds after max-position-age to fall back to taker; null = never)
    aggressive_loss_bps: 1.0           # --aggressive-loss-bps (max loss in bps accepted to avoid taker close; 0 = breakeven only)
    force_close_max_loss_bps: 0        # --force-close-max-loss-bps (progressive loss in force-close phase; 0 = disabled)
    close_spread_bps: null             # --close-spread-bps  (close order spread; null = same as spread_bps)
    close_breakeven_pct: 0.50          # --close-breakeven-pct  (fraction of max_age for breakeven tier transition)
    close_aggressive_pct: 0.75         # --close-aggressive-pct  (fraction of max_age for aggressive tier transition)
    unrealized_loss_close_bps: 0       # --unrealized-loss-close-bps  (early taker close when unrealized loss exceeds this bps; 0 = disabled)
    bbo_mode: false                    # --bbo-mode  (place orders at best bid/ask instead of mid ± spread)
    bbo_offset_bps: 0                  # --bbo-offset-bps  (bps behind BBO; 0 = at BBO)
    inventory_skew_bps: 0              # --inventory-skew-bps (skew per unit of inventory; 0 = disabled)
    coin_offset_overrides: ""          # --coin-offset-overrides  (per-coin BBO offset: "SP500:0.5,MSFT:3")
    coin_spread_overrides: ""          # --coin-spread-overrides  (per-coin spread: "SP500:8,XYZ100:15")
    coin_size_overrides: ""            # --coin-size-overrides  (per-coin order size USD: "TSLA:150,NVDA:150")
    coin_unrealized_loss_overrides: "" # --coin-unrealized-loss-overrides  (per-coin unrealized-loss bps: "INTC:25,OIL:10"; 0 disables)
    dynamic_offset_enabled: false      # --dynamic-offset  (auto-adjust offset from adverse selection tracker)
    dynamic_offset_sensitivity: 0.5    # --dynamic-offset-sensitivity  (offset widening per 1bps adverse)
    dynamic_offset_tighten_rate: 0.25  # --dynamic-offset-tighten-rate  (offset tightening for favorable fills)
    dynamic_offset_max_addition: 3.0   # --dynamic-offset-max-add  (max offset addition in bps)
    dynamic_offset_max_reduction: 1.0  # --dynamic-offset-max-reduce  (max offset reduction in bps)
    dynamic_offset_floor: 0.5          # --dynamic-offset-floor  (minimum offset bps)
    dynamic_offset_min_fills: 5        # --dynamic-offset-min-fills  (min fills before adjustment activates)
    microprice_skew_enabled: false     # --microprice-skew  (asymmetric offset based on micro-price skew)
    microprice_skew_multiplier: 1.0    # --microprice-skew-multiplier  (skew scaling factor)
    microprice_max_skew_bps: 2.0       # --microprice-max-skew-bps  (max offset adjustment from skew)
    spread_schedule: ""                # --spread-schedule  (spread multiplier: "14:1.5,0-3:1.5,22-2:2.0")
    quiet_hours_utc: ""                # --quiet-hours-utc  (UTC hours to stop/reduce quoting: "17" or "17,18")
    quiet_hours_spread_multiplier: 0   # --quiet-hours-spread-multiplier  (0 = stop, >0 = widen spread by Nx)
    drain_flag_file: ""                # --drain-flag-file  (path to flag file; presence triggers graceful drain mode)
    vol_adjust_enabled: false          # --vol-adjust  (enable volatility-adjusted BBO offset)
    vol_adjust_multiplier: 2.0         # --vol-adjust-multiplier  (offset += multiplier × avg_move_bps)
    vol_adjust_max_offset: 50          # --vol-adjust-max-offset  (max offset bps after vol adjustment)
    dynamic_age_enabled: false         # --dynamic-age  (volatility-adjusted MAX_POSITION_AGE)
    dynamic_age_baseline_vol: 1.0      # --dynamic-age-baseline-vol  (bps reference for "normal" volatility)
    dynamic_age_min: 60                # --dynamic-age-min  (minimum position age in seconds)
    dynamic_age_max: 300               # --dynamic-age-max  (maximum position age in seconds)
    auto_exclude_enabled: false        # --auto-exclude  (auto-pause coin on consecutive adverse-selection windows; requires --enable-adverse-selection-log)
    auto_exclude_threshold_bps: -3.0   # --auto-exclude-threshold-bps  (avg_<window> at or below this is "bad")
    auto_exclude_consecutive: 3        # --auto-exclude-consecutive  (consecutive bad windows required to trigger)
    auto_exclude_min_fills: 3          # --auto-exclude-min-fills  (per-window minimum fill count)
    auto_exclude_cooldown: 1800        # --auto-exclude-cooldown  (pause seconds after trigger; auto-resume)
    auto_exclude_window_label: "60s"   # --auto-exclude-window-label  (5s|30s|60s tracker sample window)
    forager_enabled: false             # --forager  (composite-score auto-exclude on activity + close-quality + cost)
    forager_score_threshold: 30.0      # --forager-threshold  (composite score below this triggers; 0-100)
    forager_consecutive: 3             # --forager-consecutive  (consecutive sub-threshold checks to trigger)
    forager_cooldown_seconds: 1800     # --forager-cooldown  (pause seconds after trigger; auto-resume)
    forager_weight_activity: 0.3       # --forager-w-activity  (composite weight for activity dimension)
    forager_weight_quality: 0.4        # --forager-w-quality  (composite weight for close-quality dimension)
    forager_weight_cost: 0.3           # --forager-w-cost  (composite weight for cost dimension)
    forager_window_seconds: 1800.0     # env-only (rolling window for fill activity + close history)
    forager_check_interval_seconds: 300.0  # env-only (per-coin throttle between health checks)
    forager_activity_idle_min_seconds: 300.0  # env-only (idle grace before activity score decays)
    forager_cost_max_per_1k: 0.6       # env-only ($/1K at which cost score reaches 0)
    forager_min_closes_for_quality: 5  # env-only (min closes required to trust quality dimension)
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
  meta_cache_ttl: 3600            # env META_CACHE_TTL  (seconds; asset metadata cache)
  mids_cache_ttl: 5.0             # env MIDS_CACHE_TTL  (seconds; mid price cache)

margin_validation:
  min_order_value_default: 50     # env MIN_ORDER_VALUE_DEFAULT
  min_order_value_btc: 100        # env MIN_ORDER_VALUE_BTC
  min_order_value_eth: 100        # env MIN_ORDER_VALUE_ETH
  initial_margin_multiplier: 3.0  # env INITIAL_MARGIN_MULTIPLIER
  margin_safety_buffer: 1.5       # env MARGIN_SAFETY_BUFFER

logging:
  log_format: "text"              # env LOG_FORMAT  (text|json)
  log_level: "INFO"               # env LOG_LEVEL  (DEBUG|INFO|WARNING|ERROR|CRITICAL)

hip3:
  env:
    TRADING_DEXES: ""             # Comma-separated DEX names (e.g. "xyz,flx")
    ENABLE_STANDARD_HL: "true"    # Trade standard HL perps alongside HIP-3
    "{DEX}_COINS": ""             # Per-DEX coin list (e.g. XYZ_COINS=XYZ100,XYZ200)
  cli:
    --dex: []                     # HIP-3 DEX names (overrides TRADING_DEXES)
    --no-hl: false                # Disable standard HL perps
    --enable-ws: false            # Enable WebSocket L2 book feed + WS guards

ws_guards:                         # All require --enable-ws
  bbo_guard_threshold_bps: 2.0     # --bbo-guard-threshold-bps  (cancel entry orders on BBO change; 0 = disabled)
  imbalance_guard_threshold: 0     # --imbalance-guard-threshold  (cancel one side on L2 skew; 0 = disabled)
  imbalance_guard_depth: 5         # --imbalance-guard-depth  (L2 levels for imbalance calc)
  close_refresh_threshold_bps: 0   # --close-refresh-threshold-bps  (refresh close orders on BBO change; 0 = disabled)
  velocity_guard_enabled: false    # --velocity-guard  (cancel one side on sustained BBO direction; disabled by default)
  velocity_consecutive: 3          # --velocity-consecutive  (consecutive same-direction moves to trigger)
  velocity_min_move_bps: 1.0       # --velocity-min-move-bps  (min cumulative move in bps to trigger)
  enable_adverse_selection_log: false  # --enable-adverse-selection-log  (post-fill mid tracking)
  adverse_selection_log_interval: 300  # --adverse-selection-log-interval  (summary log interval in seconds)

config_merge_order: "default_configs[strategy] ← CLI overrides (only non-null)"
priority: "CLI flag > env var > default_configs > strategy constructor fallback"
```
