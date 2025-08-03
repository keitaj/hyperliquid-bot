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

## 機能

- **Market Data**: リアルタイム価格、オーダーブック、ローソク足データの取得
- **Order Management**: 指値注文、成行注文の発注とキャンセル
- **Risk Management**: レバレッジ制限、最大ドローダウン、日次損失制限
- **Multiple Strategies**: 6つの異なる取引戦略から選択可能

## 取引戦略

### 1. Simple MA Strategy (`simple_ma`)
- 短期・長期移動平均のクロスオーバー
- ゴールデンクロスで買い、デッドクロスで売り
- パラメータ: `fast_ma_period=10`, `slow_ma_period=30`

### 2. RSI Strategy (`rsi`)
- 相対力指数による買われすぎ・売られすぎの判断
- RSI < 30で買い、RSI > 70で売り
- パラメータ: `rsi_period=14`, `oversold=30`, `overbought=70`

### 3. Bollinger Bands Strategy (`bollinger_bands`)
- ボリンジャーバンドによる価格の乖離を利用
- 下限バンドタッチで買い、上限バンドタッチで売り
- ボラティリティ拡大時のブレイクアウト検出
- パラメータ: `bb_period=20`, `std_dev=2`

### 4. MACD Strategy (`macd`)
- MACD線とシグナル線のクロスオーバー
- ダイバージェンス（逆行現象）の検出機能
- パラメータ: `fast_ema=12`, `slow_ema=26`, `signal_ema=9`

### 5. Grid Trading Strategy (`grid_trading`)
- レンジ相場で一定間隔の買い・売り注文を配置
- 価格が上下するたびに利益を積み重ねる
- パラメータ: `grid_levels=10`, `grid_spacing_pct=0.5%`

### 6. Breakout Strategy (`breakout`)
- サポート・レジスタンスラインのブレイクアウトを検出
- 出来高確認とATRによるストップロス管理
- パラメータ: `lookback_period=20`, `volume_multiplier=1.5`

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

## 注意事項

- 本番環境で使用する前に、必ずテストネットで動作確認してください
- 秘密鍵は安全に管理してください
- リスク管理パラメータは慎重に設定してください