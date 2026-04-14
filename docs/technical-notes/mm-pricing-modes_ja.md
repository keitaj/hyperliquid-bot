# マーケットメイキング 価格決定モード

[English](mm-pricing-modes.md) | **日本語**

## 概要

マーケットメイキング戦略は、買い/売り注文の配置場所を決定する3つの価格決定モードをサポートしています。これらは組み合わせて使用できます。

## 1. Mid ± Spread (デフォルト)

中間価格を中心に対称的に注文を配置します。

```
            spread_bps
         ◄────────────────►
         
  BUY                          SELL
   │    best    mid    best     │
   ▼    bid      │     ask      ▼
───┼─────┼───────┼───────┼──────┼───► 価格
   │     │               │     │
   mid - spread          mid + spread
```

**設定:** `--spread-bps 10`

**タイトスプレッド市場での問題:** 市場スプレッドが 0.1-2bps の場合、`mid ± 10bps` の注文は BBO から 5-10bps 離れた場所に配置され、約定しません。

## 2. BBO 追従モード

mid ± spread の代わりに、最良気配 (best bid/ask) の付近に注文を配置します。

```
  bbo_offset          bbo_offset
  ◄──►                      ◄──►
  
  BUY   best           best   SELL
   │    bid             ask     │
   ▼     │               │     ▼
───┼─────┼───────────────┼─────┼───► 価格
   │     │   市場         │     │
   bid - offset  スプレッド  ask + offset
```

**設定:** `--bbo-mode --bbo-offset-bps 1`

**仕組み:**
- `buy_price = best_bid × (1 - offset / 10000)`
- `sell_price = best_ask × (1 + offset / 10000)`
- BBO が取得できない場合 (bid/ask = 0) は mid ± spread にフォールバック
- `--maker-only` 時はデフォルト 0.1bps のオフセットで Alo リジェクションリスクを軽減

**実績:** 約定率が 0.5% → 1-2.4% に改善。

## 3. 在庫スキュー

現在のポジション（在庫）に基づいて buy/sell の両方の価格をシフトし、ポジション解消を促進します。Avellaneda-Stoikov モデルに基づいています。

### ポジションなし（対称）

```
         BUY              SELL
          │    mid          │
          ▼     │           ▼
──────────┼─────┼───────────┼──────► 価格
```

### ロングポジション（下方シフト → 売りが約定しやすくなる）

```
    BUY          SELL
     │    mid     │
     ▼     │      ▼
─────┼─────┼──────┼────────────────► 価格
     │◄──── skew ────►│
         両方の価格が下方にシフト
```

売り価格が mid に近づくため約定しやすくなり、市場がロング在庫を引き取ることを促します。

### ショートポジション（上方シフト → 買いが約定しやすくなる）

```
                BUY           SELL
         mid     │              │
          │      ▼              ▼
──────────┼──────┼──────────────┼──► 価格
              │◄──── skew ────►│
              両方の価格が上方にシフト
```

**設定:** `--inventory-skew-bps 2`

**スキューの計算方法:**

```
normalized_position = min(position_value / order_size_usd, inventory_skew_cap)
skew_bps = direction × normalized_position × inventory_skew_bps

direction: ロング = +1（下方シフト）, ショート = -1（上方シフト）
```

**`inventory_skew_bps=2` の例:**

| ポジション | 正規化 | スキュー | 効果 |
|-----------|--------|---------|------|
| ポジションなし | 0 | 0 bps | 対称 |
| $100 ロング (0.5x) | 0.5 | +1 bps | わずかに売り寄り |
| $200 ロング (1x) | 1.0 | +2 bps | 適度に売り寄り |
| $400 ロング (2x) | 2.0 | +4 bps | 強く売り寄り |
| $600+ ロング (3x+) | 3.0 (上限) | +6 bps | 最大売り寄り |

## 組み合わせ: BBO + 在庫スキュー

本番推奨設定は両方のモードを組み合わせます:

```
--bbo-mode --bbo-offset-bps 1 --inventory-skew-bps 2
```

**注文フロー:**

```
1. ベース価格を計算
   ├─ BBO モード ON  → buy = bid - offset, sell = ask + offset
   └─ BBO モード OFF → buy = mid - spread, sell = mid + spread

2. 在庫スキューを適用（ポジションがある場合）
   └─ ポジション解消方向に skew_bps 分シフト

3. Alo（maker-only）指値注文として発注
```

**なぜこの組み合わせが効果的か:**
- **エントリー（BBO）:** 板の最前列に注文 → 高い約定率
- **エグジット（take-profit）:** `spread_bps`（例: 10bps）で利益幅を確保
- **在庫管理（スキュー）:** 自動的にポジション解消方向にバイアス → 高コストな taker force-close を削減

## 設定リファレンス

| パラメータ | CLI フラグ | デフォルト | 説明 |
|-----------|----------|---------|------|
| `spread_bps` | `--spread-bps` | 5 | mid からのスプレッド (BBO モードのフォールバック) |
| `bbo_mode` | `--bbo-mode` | false | mid ± spread の代わりに BBO に配置 |
| `bbo_offset_bps` | `--bbo-offset-bps` | 0 (maker_only時 0.1) | BBO からの距離 (bps) |
| `inventory_skew_bps` | `--inventory-skew-bps` | 0 | 正規化在庫1単位あたりのスキュー |
| `inventory_skew_cap` | config のみ | 3.0 | スキュー計算の正規化ポジション上限 |
