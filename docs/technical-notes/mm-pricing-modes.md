# Market Making Pricing Modes

**English** | [日本語](mm-pricing-modes_ja.md)

## Overview

The market making strategy supports three pricing modes that determine where buy/sell orders are placed. These can be combined for optimal performance.

## 1. Mid ± Spread (Default)

Orders are placed symmetrically around the mid price.

```
            spread_bps
         ◄────────────────►
         
  BUY                          SELL
   │    best    mid    best     │
   ▼    bid      │     ask      ▼
───┼─────┼───────┼───────┼──────┼───► price
   │     │               │     │
   mid - spread          mid + spread
```

**Config:** `--spread-bps 10`

**Problem on tight-spread markets:** When market spread is 0.1-2bps, placing at `mid ± 10bps` puts orders 5-10bps away from BBO — far too distant to get filled.

## 2. BBO-Following Mode

Orders are placed at or near the best bid/ask instead of mid ± spread.

```
  bbo_offset          bbo_offset
  ◄──►                      ◄──►
  
  BUY   best           best   SELL
   │    bid             ask     │
   ▼     │               │     ▼
───┼─────┼───────────────┼─────┼───► price
   │     │   market      │     │
   bid - offset  spread  ask + offset
```

**Config:** `--bbo-mode --bbo-offset-bps 1`

**How it works:**
- `buy_price = best_bid × (1 - offset / 10000)`
- `sell_price = best_ask × (1 + offset / 10000)`
- Falls back to mid ± spread when BBO is unavailable (bid/ask = 0)
- When `--maker-only`, defaults to 0.1bps offset to avoid Alo rejection

**Result:** Fill rate improved from 0.5% to 1-2.4% in production.

## 3. Inventory Skew

Shifts both buy and sell prices based on current inventory to encourage position reduction. Based on the Avellaneda-Stoikov model.

### No Position (Symmetric)

```
         BUY              SELL
          │    mid          │
          ▼     │           ▼
──────────┼─────┼───────────┼──────► price
```

### Long Position (Shift Down → Sell More Attractive)

```
    BUY          SELL
     │    mid     │
     ▼     │      ▼
─────┼─────┼──────┼────────────────► price
     │◄──── skew ────►│
         both prices shift down
```

The sell price moves closer to mid (easier to fill), encouraging the market to take our long inventory.

### Short Position (Shift Up → Buy More Attractive)

```
                BUY           SELL
         mid     │              │
          │      ▼              ▼
──────────┼──────┼──────────────┼──► price
              │◄──── skew ────►│
              both prices shift up
```

**Config:** `--inventory-skew-bps 2`

**How skew is calculated:**

```
normalized_position = min(position_value / order_size_usd, inventory_skew_cap)
skew_bps = direction × normalized_position × inventory_skew_bps

direction: +1 for long (shift down), -1 for short (shift up)
```

**Example with `inventory_skew_bps=2`:**

| Position | Normalized | Skew | Effect |
|----------|-----------|------|--------|
| No position | 0 | 0 bps | Symmetric |
| $100 long (0.5x) | 0.5 | +1 bps | Slight sell bias |
| $200 long (1x) | 1.0 | +2 bps | Moderate sell bias |
| $400 long (2x) | 2.0 | +4 bps | Strong sell bias |
| $600+ long (3x+) | 3.0 (capped) | +6 bps | Maximum sell bias |

## Combined: BBO + Inventory Skew

The recommended production configuration uses both modes together:

```
--bbo-mode --bbo-offset-bps 1 --inventory-skew-bps 2
```

**Order flow:**

```
1. Calculate base prices
   ├─ BBO mode ON  → buy = bid - offset, sell = ask + offset
   └─ BBO mode OFF → buy = mid - spread, sell = mid + spread

2. Apply inventory skew (if position exists)
   └─ shift both prices by skew_bps in position-reducing direction

3. Place orders as Alo (maker-only) limit orders
```

**Why this works:**
- **Entry (BBO):** Orders at the front of the book → high fill rate
- **Exit (take-profit):** Uses `spread_bps` (e.g., 10bps) for profit margin
- **Inventory management (skew):** Automatically biases toward reducing positions → fewer expensive taker force-closes

## Configuration Reference

| Parameter | CLI Flag | Default | Description |
|-----------|----------|---------|-------------|
| `spread_bps` | `--spread-bps` | 5 | Spread from mid in bps (fallback for BBO mode) |
| `bbo_mode` | `--bbo-mode` | false | Place at BBO instead of mid ± spread |
| `bbo_offset_bps` | `--bbo-offset-bps` | 0 (0.1 if maker_only) | Distance behind BBO in bps |
| `inventory_skew_bps` | `--inventory-skew-bps` | 0 | Skew per unit of normalized inventory |
| `inventory_skew_cap` | config only | 3.0 | Max normalized position for skew calculation |
