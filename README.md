# Hyperliquid Trading Bot

Hyperliquid DEX用の自動取引ボットです。

## セットアップ

1. 依存関係のインストール:
```bash
pip3 install -r requirements.txt
```

2. 環境変数の設定:
`.env.example`を`.env`にコピー:
```bash
cp .env.example .env
```

3. APIキーの設定:

`.env`ファイルに以下の情報を設定します：
- `HYPERLIQUID_ACCOUNT_ADDRESS`: ウォレットアドレス
- `HYPERLIQUID_PRIVATE_KEY`: Private key
- `USE_TESTNET`: テストネットを使用する場合は`true`

### 方法1: ウォレットのprivate keyを直接使用
自分のウォレットのprivate keyを直接設定します。

### 方法2: APIウォレットを使用（推奨）
より安全な方法として、[https://app.hyperliquid.xyz/API](https://app.hyperliquid.xyz/API) にアクセスしてAPIウォレットを生成

**注意**: APIウォレットを使用する場合、取引に必要な資金をAPIウォレットに転送する必要があります。

## 使い方

### 基本的な使い方
```bash
# デフォルト戦略（Simple MA）で起動
python3 bot.py

# 特定の戦略を指定して起動
python3 bot.py --strategy rsi

# 取引対象通貨を指定
python3 bot.py --strategy macd --coins BTC ETH

# ヘルプを表示
python3 bot.py --help
```

### パラメーターのカスタマイズ
各戦略のパラメーターをコマンドラインから指定できます：

#### 共通パラメーター
```bash
# ポジションサイズと損益設定を変更
python3 bot.py --position-size-usd 200 --take-profit-percent 10 --stop-loss-percent 3
```

#### Simple MA戦略
```bash
# 移動平均期間をカスタマイズ
python3 bot.py --strategy simple_ma --fast-ma-period 5 --slow-ma-period 20
```

#### RSI戦略
```bash
# RSI閾値をカスタマイズ
python3 bot.py --strategy rsi --rsi-period 21 --oversold-threshold 25 --overbought-threshold 75
```

#### Bollinger Bands戦略
```bash
# ボリンジャーバンド設定を変更
python3 bot.py --strategy bollinger_bands --bb-period 25 --std-dev 2.5
```

#### MACD戦略
```bash
# MACD期間を調整
python3 bot.py --strategy macd --fast-ema 10 --slow-ema 20 --signal-ema 7
```

#### Grid Trading戦略
```bash
# グリッド設定をカスタマイズ
python3 bot.py --strategy grid_trading --grid-levels 15 --grid-spacing-pct 0.3 --position-size-per-grid 30
```

#### Breakout戦略
```bash
# ブレイクアウト検出パラメーター
python3 bot.py --strategy breakout --lookback-period 30 --volume-multiplier 2.0 --atr-period 20
```

### 残高・ポジション確認
```bash
# アカウント残高とポジションを確認
python3 check_balance.py
```

実行例:
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

## 機能

- **Market Data**: リアルタイム価格、オーダーブック、ローソク足データの取得
- **Order Management**: 指値注文、成行注文の発注とキャンセル
- **Risk Management**: レバレッジ制限、最大ドローダウン、日次損失制限
- **Multiple Strategies**: 6つの異なる取引戦略から選択可能

## 取引戦略

### 1. Simple MA Strategy (`simple_ma`)
- 短期・長期移動平均のクロスオーバー
- ゴールデンクロスで買い、デッドクロスで売り
- デフォルトパラメータ: `fast_ma_period=10`, `slow_ma_period=30`

#### コマンドラインパラメータ:
- `--fast-ma-period`: 短期移動平均の期間（デフォルト: 10）
- `--slow-ma-period`: 長期移動平均の期間（デフォルト: 30）

### 2. RSI Strategy (`rsi`)
- 相対力指数による買われすぎ・売られすぎの判断
- RSI < 30で買い、RSI > 70で売り
- デフォルトパラメータ: `rsi_period=14`, `oversold=30`, `overbought=70`

#### コマンドラインパラメータ:
- `--rsi-period`: RSI計算期間（デフォルト: 14）
- `--oversold-threshold`: 売られすぎ判定の閾値（デフォルト: 30）
- `--overbought-threshold`: 買われすぎ判定の閾値（デフォルト: 70）

### 3. Bollinger Bands Strategy (`bollinger_bands`)
- ボリンジャーバンドによる価格の乖離を利用
- 下限バンドタッチで買い、上限バンドタッチで売り
- ボラティリティ拡大時のブレイクアウト検出
- デフォルトパラメータ: `bb_period=20`, `std_dev=2`, `squeeze_threshold=0.02`

#### コマンドラインパラメータ:
- `--bb-period`: ボリンジャーバンドの計算期間（デフォルト: 20）
- `--std-dev`: 標準偏差の倍数（デフォルト: 2）
- `--squeeze-threshold`: スクイーズ判定の閾値（デフォルト: 0.02）

### 4. MACD Strategy (`macd`)
- MACD線とシグナル線のクロスオーバー
- ダイバージェンス（逆行現象）の検出機能
- デフォルトパラメータ: `fast_ema=12`, `slow_ema=26`, `signal_ema=9`

#### コマンドラインパラメータ:
- `--fast-ema`: 短期EMAの期間（デフォルト: 12）
- `--slow-ema`: 長期EMAの期間（デフォルト: 26）
- `--signal-ema`: シグナル線EMAの期間（デフォルト: 9）

### 5. Grid Trading Strategy (`grid_trading`)
- レンジ相場で一定間隔の買い・売り注文を配置
- 価格が上下するたびに利益を積み重ねる
- デフォルトパラメータ: `grid_levels=10`, `grid_spacing_pct=0.5%`, `range_period=100`

#### コマンドラインパラメータ:
- `--grid-levels`: グリッドのレベル数（デフォルト: 10）
- `--grid-spacing-pct`: グリッド間隔のパーセンテージ（デフォルト: 0.5）
- `--position-size-per-grid`: 各グリッドのポジションサイズ（デフォルト: 50）
- `--range-period`: レンジ計算期間（デフォルト: 100）

### 6. Breakout Strategy (`breakout`)
- サポート・レジスタンスラインのブレイクアウトを検出
- 出来高確認とATRによるストップロス管理
- デフォルトパラメータ: `lookback_period=20`, `volume_multiplier=1.5`, `atr_period=14`

#### コマンドラインパラメータ:
- `--lookback-period`: サポート・レジスタンス計算期間（デフォルト: 20）
- `--volume-multiplier`: 出来高確認の倍率（デフォルト: 1.5）
- `--breakout-confirmation-bars`: ブレイクアウト確認に必要なバー数（デフォルト: 2）
- `--atr-period`: ATR計算期間（デフォルト: 14）

## 技術ドキュメント

より詳細な技術情報については、以下のドキュメントを参照してください：

- [タイムフレームとパラメータの詳細](./docs/technical-notes/timeframes.md) - 各戦略のタイムフレームとパラメータ単位の説明

## ファイル構成

- `bot.py`: メインのボットクラス
- `config.py`: 設定管理
- `market_data.py`: マーケットデータの取得
- `order_manager.py`: 注文管理
- `risk_manager.py`: リスク管理
- `strategies/`: 取引戦略
  - `base_strategy.py`: 戦略の基底クラス
  - `simple_ma_strategy.py`: 移動平均戦略
  - `rsi_strategy.py`: RSI戦略
  - `bollinger_bands_strategy.py`: ボリンジャーバンド戦略
  - `macd_strategy.py`: MACD戦略
  - `grid_trading_strategy.py`: グリッド取引戦略
  - `breakout_strategy.py`: ブレイクアウト戦略
- `docs/`: ドキュメント
  - `technical-notes/`: 技術的な詳細ドキュメント

## 注意事項

- 本番環境で使用する前に、必ずテストネットで動作確認してください
- 秘密鍵は安全に管理してください
- リスク管理パラメータは慎重に設定してください

## ⚠️ 重要な免責事項

**このソフトウェアは教育および情報提供のみを目的としています。**

本ソフトウェアの使用により生じるいかなる金銭的損失についても、作者は一切の責任を負いません。仮想通貨取引は大きなリスクを伴います。実際の取引を行う前に、必ず以下をご確認ください：

- コードを十分に理解し、テストしてください
- 少額またはテストネットで動作を確認してください
- 自己の責任において使用してください
- 投資判断の前に専門家に相談することをお勧めします

詳細な免責事項は [LICENSE](./LICENSE) ファイルをご確認ください。