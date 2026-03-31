# Risk Manager State Machine

**English** | [日本語](risk-state-machine_ja.md)

## Overview

The risk management system determines what the bot does each trading cycle. It evaluates multiple conditions simultaneously, and when conflicts arise, the most severe action always wins.

## Risk Levels (RISK_LEVEL env var)

Reloadable at runtime without restarting the bot.

| Level    | Size Multiplier | Behavior                        |
|----------|----------------:|----------------------------------|
| `green`  | 1.0x            | Normal trading                   |
| `yellow` | 0.5x            | Half position sizes              |
| `red`    | 0.0x            | Pause — no new positions         |
| `black`  | 0.0x            | Emergency — close all positions  |

## Actions (priority order)

When multiple conditions fire simultaneously, the highest-priority action wins.

```
Priority  Action             Effect
──────────────────────────────────────────────────────────
  6       stop_bot           Close all positions, stop the bot
  5       force_close        Close all positions, record emergency stop
  4       close_all          Close all positions (RISK_LEVEL=black)
  3       pause              Cancel all orders, wait (RISK_LEVEL=red)
  2       cooldown           Wait for cooldown timer to expire
  1       block_new_orders   Manage existing positions only, no new orders
  0       none               Normal trading
```

## State Transition Diagram

```
                         ┌─────────────────────────────┐
                         │         NORMAL (none)        │
                         │  - Execute strategy signals  │
                         │  - Update order status       │
                         │  - Check per-trade stops     │
                         └──────────┬──────────────────┘
                                    │
                    ┌───────────────┼───────────────────┐
                    │               │                   │
                    ▼               ▼                   ▼
    ┌───────────────────┐ ┌─────────────────┐ ┌────────────────────┐
    │  BLOCK_NEW_ORDERS │ │     PAUSE       │ │     COOLDOWN       │
    │                   │ │                 │ │                    │
    │  Triggers:        │ │  Trigger:       │ │  Trigger:          │
    │  - Leverage high  │ │  - RISK_LEVEL   │ │  - After emergency │
    │  - Margin high    │ │    = red        │ │    stop            │
    │  - Drawdown       │ │                 │ │                    │
    │  - Daily loss %   │ │  Effect:        │ │  Effect:           │
    │  - Max positions  │ │  - Cancel all   │ │  - Cancel all      │
    │  - No metrics     │ │    orders       │ │    orders          │
    │                   │ │  - Wait         │ │  - Wait N seconds  │
    │  Effect:          │ │                 │ │    (default 3600)  │
    │  - No new orders  │ │                 │ │                    │
    │  - Manage existing│ │                 │ │  Recovery:         │
    │  - Per-trade stops│ │                 │ │  - Timer expires   │
    └───────────────────┘ └─────────────────┘ └────────────────────┘
                    │               │                   │
                    │               │                   │
                    ▼               ▼                   ▼
    ┌───────────────────┐ ┌─────────────────┐ ┌────────────────────┐
    │    CLOSE_ALL      │ │  FORCE_CLOSE    │ │     STOP_BOT       │
    │                   │ │                 │ │                    │
    │  Trigger:         │ │  Trigger:       │ │  Trigger:          │
    │  - RISK_LEVEL     │ │  - margin_ratio │ │  - Daily loss $    │
    │    = black        │ │    >= threshold │ │    >= limit        │
    │                   │ │                 │ │                    │
    │  Effect:          │ │  Effect:        │ │  Effect:           │
    │  - Close all      │ │  - Close all    │ │  - Close all       │
    │    positions      │ │    positions    │ │    positions       │
    │                   │ │  - Record       │ │  - Record          │
    │                   │ │    emergency    │ │    emergency stop  │
    │                   │ │    stop         │ │  - running = false │
    │                   │ │  - Enter        │ │  - Bot exits       │
    │                   │ │    cooldown     │ │                    │
    └───────────────────┘ └─────────────────┘ └────────────────────┘
```

## Trigger Conditions

### Metrics-based checks (evaluated every cycle)

| Check               | Condition                                          | Default Threshold |
|----------------------|----------------------------------------------------|-------------------|
| Leverage             | `leverage > max_leverage`                          | 3.0               |
| Margin ratio         | `margin_ratio >= max_margin_usage`                 | 0.8 (80%)         |
| Drawdown             | `(starting - current) / starting > max_drawdown`   | 0.1 (10%)         |
| Daily loss %         | `(daily_start - current) / daily_start > limit`    | 0.05 (5%)         |
| Max positions        | `num_positions > max_open_positions`               | 5                 |

### Opt-in checks (disabled by default)

| Check                | Condition                                          | Config Key           |
|----------------------|----------------------------------------------------|----------------------|
| Force close margin   | `margin_ratio >= FORCE_CLOSE_MARGIN`               | `FORCE_CLOSE_MARGIN` |
| Daily loss (absolute)| `daily_loss_usd >= DAILY_LOSS_LIMIT`               | `DAILY_LOSS_LIMIT`   |
| Per-trade stop loss  | `trade_loss_pct >= PER_TRADE_STOP_LOSS`            | `PER_TRADE_STOP_LOSS`|

### Safety guard

A balance change exceeding 50% in a single cycle is treated as a data issue (e.g., spot API 429) and ignored to prevent false triggers.

## Per-Trade Stop Loss

Evaluated independently from the main risk check. Each cycle, all open positions are scanned:

```
For each position:
  loss_pct = |unrealized_pnl| / position_value   (if pnl < 0)
  if loss_pct >= PER_TRADE_STOP_LOSS:
    → market-close that position
```

## Circuit Breaker Integration

The bot wraps risk checks and strategy execution with a circuit breaker:

- **risk_metrics**: If `check_risk_limits()` returns "No metrics available" for 5+ consecutive cycles, all orders are cancelled.
- **strategy**: If strategy execution throws 5+ consecutive errors, signal generation is skipped until auto-recovery (60s).

## Recovery Paths

| From State       | Recovery Condition                                    |
|------------------|-------------------------------------------------------|
| block_new_orders | All metric checks pass again                         |
| pause            | RISK_LEVEL changed from `red` to `green`/`yellow`    |
| cooldown         | `cooldown_after_stop` seconds elapsed (default 3600)  |
| close_all        | RISK_LEVEL changed from `black`                       |
| force_close      | Margin ratio drops below threshold + cooldown expires |
| stop_bot         | Manual restart required                               |

## Trading Loop Flow

```
_trading_loop():
  1. check_risk_limits()
  2. If any check failed:
       cancel_all_orders()
       Execute highest-priority action (stop_bot > force_close > close_all > ...)
       return
  3. update_order_status()
  4. check_per_trade_stops()     ← close individual losing positions
  5. strategy.run(coins)          ← generate signals + place orders
  6. Log risk summary (every 60s)
```
