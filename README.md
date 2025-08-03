# Hyperliquid Trading Bot

**English** | [日本語](README_ja.md)

Automated trading bot for Hyperliquid DEX.

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
- [Trading Strategies](#trading-strategies)
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
- `HYPERLIQUID_ACCOUNT_ADDRESS`: Wallet address
- `HYPERLIQUID_PRIVATE_KEY`: Private key
- `USE_TESTNET`: Set to `true` to use testnet

### Method 1: Direct Private Key Usage
Set your wallet's private key directly.

### Method 2: API Wallet Usage (Recommended)
For a more secure approach, visit [https://app.hyperliquid.xyz/API](https://app.hyperliquid.xyz/API) to generate an API wallet.

**Note**: When using an API wallet, you need to transfer the necessary funds for trading to the API wallet.

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
💰 Account Value:    $299.00
✅ Available:        $299.00
🔒 Margin Used:      $0.00
📈 Position Value:   $0.00

==================================================
📋 POSITIONS
==================================================
No open positions
==================================================
```

## Features

- **Market Data**: Real-time price, order book, and candlestick data retrieval
- **Order Management**: Limit and market order placement and cancellation
- **Risk Management**: Leverage limits, maximum drawdown, daily loss limits
- **Multiple Strategies**: Choose from 6 different trading strategies

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
- `strategies/`: Trading strategies
  - `base_strategy.py`: Base strategy class
  - `simple_ma_strategy.py`: Moving average strategy
  - `rsi_strategy.py`: RSI strategy
  - `bollinger_bands_strategy.py`: Bollinger Bands strategy
  - `macd_strategy.py`: MACD strategy
  - `grid_trading_strategy.py`: Grid trading strategy
  - `breakout_strategy.py`: Breakout strategy
- `docs/`: Documentation
  - `technical-notes/`: Technical detail documents

## Notes

- Before using in production, always test on testnet first
- Keep your private keys secure
- Set risk management parameters carefully