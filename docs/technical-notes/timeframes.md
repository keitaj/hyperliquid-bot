# Timeframes and Parameters Details

**English** | [日本語](timeframes_ja.md)

## Overview

This document explains the timeframes (candlestick intervals) used by each trading strategy and the units of parameters.

## Parameter Units

**Important**: All period parameters (period, ma_period, ema, etc.) represent "number of candlesticks". The actual time is determined by the timeframe used by each strategy.

## Timeframes for Each Strategy

### 1. Simple MA Strategy - 5-minute timeframe

- **Timeframe**: 5-minute (5m)
- **Parameter to actual time relationship**:
  - `fast_ma_period=10` → 10 candles × 5 minutes = 50 minutes
  - `slow_ma_period=30` → 30 candles × 5 minutes = 150 minutes (2.5 hours)

**Rationale**:
- Moving average crossover is a simple strategy, suitable for high-frequency trading signals
- Quickly catches short-term trend changes
- Suitable for day trading and scalping

### 2. RSI Strategy - 15-minute timeframe

- **Timeframe**: 15-minute (15m)
- **Parameter to actual time relationship**:
  - `rsi_period=14` → 14 candles × 15 minutes = 210 minutes (3.5 hours)

**Rationale**:
- RSI measures relative strength, requiring a reasonable period
- 15-minute timeframe reduces market noise and generates reliable signals
- Provides adequate trading opportunities while avoiding excessive trading

### 3. Bollinger Bands Strategy - 15-minute timeframe

- **Timeframe**: 15-minute (15m)
- **Parameter to actual time relationship**:
  - `bb_period=20` → 20 candles × 15 minutes = 300 minutes (5 hours)

**Rationale**:
- Accurate volatility measurement requires an adequate period
- 15-minute timeframe smooths price fluctuations and improves band reliability
- Suitable for detecting squeezes and breakouts

### 4. MACD Strategy - 15-minute timeframe

- **Timeframe**: 15-minute (15m)
- **Parameter to actual time relationship**:
  - `fast_ema=12` → 12 candles × 15 minutes = 180 minutes (3 hours)
  - `slow_ema=26` → 26 candles × 15 minutes = 390 minutes (6.5 hours)
  - `signal_ema=9` → 9 candles × 15 minutes = 135 minutes (2.25 hours)

**Rationale**:
- MACD captures medium-term trends and momentum
- 15-minute timeframe achieves a balance between responsiveness and reliability
- Provides sufficient period for divergence detection

### 5. Grid Trading Strategy

- **Timeframe**: Variable depending on strategy implementation
- **Parameters**: `range_period` is the period for range calculation

**Rationale**:
- Grid trading functions in ranging markets, adjustable according to market conditions
- Requires understanding of long-term price ranges

### 6. Breakout Strategy

- **Timeframe**: Variable depending on strategy implementation
- **Parameters and usage**:
  - `lookback_period=20` → Support/resistance line calculation period
  - `atr_period=14` → Volatility measurement period

**Rationale**:
- Breakouts occur across various timeframes
- Optimal timeframe selection depends on market conditions

## General Principles of Timeframe Selection

### Short Timeframes (1-5 minutes)
- **Advantages**: 
  - Quick response
  - Many trading opportunities
  - Captures small price movements
- **Disadvantages**: 
  - High noise
  - Increased false signals
  - Higher trading costs

### Medium Timeframes (15 minutes - 1 hour)
- **Advantages**: 
  - Good balance between noise and signals
  - Moderate trading frequency
  - Higher trend reliability
- **Disadvantages**: 
  - Somewhat slower response
  - May miss short-term opportunities

### Long Timeframes (4 hours - daily)
- **Advantages**: 
  - Very reliable signals
  - Captures major trends
  - Lower trading costs
- **Disadvantages**: 
  - Slow response
  - Fewer trading opportunities
  - Potential for large drawdowns

## Customization Recommendations

1. **Adjustment based on trading style**:
   - Scalping: Consider shorter timeframes
   - Swing trading: Consider longer timeframes

2. **Adjustment based on market conditions**:
   - High volatility: Longer timeframes to reduce noise
   - Low volatility: Shorter timeframes to capture small movements

3. **Adjustment based on risk tolerance**:
   - Low risk: Long timeframes, fewer trades
   - High risk: Short timeframes, frequent trades

## Important Notes

- When changing parameters, be aware of the conversion to actual time
- Recommend validating optimal parameters through backtesting
- Regularly review parameters according to changing market conditions