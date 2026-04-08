# Hyperliquid Trading Bot

[English](README.md) | **日本語**

Hyperliquid DEX用の自動取引ボットです。**HIP-3マルチDEX対応**（trade.xyz、Felix、Markets by Kinetiq、Basedなど）。

## ⚠️ 重要な免責事項

**このソフトウェアは教育および情報提供のみを目的としています。**

本ソフトウェアの使用により生じるいかなる金銭的損失についても、作者は一切の責任を負いません。仮想通貨取引は大きなリスクを伴います。実際の取引を行う前に、必ず以下をご確認ください：

- コードを十分に理解し、テストしてください
- 少額またはテストネットで動作を確認してください
- 自己の責任において使用してください
- 投資判断の前に専門家に相談することをお勧めします

詳細な免責事項は [LICENSE](./LICENSE) ファイルをご確認ください。

---

## 📋 目次

- [セットアップ](#セットアップ)
- [使い方](#使い方)
  - [Docker での使い方（推奨）](#-docker-での使い方推奨)
  - [Python での使い方](#-python-での使い方)
- [HIP-3 マルチDEX取引](#hip-3-マルチdex取引)
- [取引戦略](#取引戦略)
- [リスクガードレール](#リスクガードレール)
- [機能](#機能)
- [技術ドキュメント](#技術ドキュメント)
- [ファイル構成](#ファイル構成)
- [パラメータリファレンス（AI向け）](#パラメータリファレンスai向け)

## セットアップ

### 環境変数ファイルの作成

```bash
cp .env.example .env
```

### APIキーの設定

`.env`ファイルを編集して以下の情報を設定します：

**設定項目:**
- `HYPERLIQUID_ACCOUNT_ADDRESS`: メインウォレットアドレス（資金を保有するアドレス）
- `HYPERLIQUID_PRIVATE_KEY`: トランザクション署名用のPrivate key
- `USE_TESTNET`: テストネットを使用する場合は`true`

### 方法1: ウォレットのprivate keyを直接使用
自分のウォレットのprivate keyを直接設定します。

### 方法2: APIウォレットを使用（推奨）
より安全な方法として、[https://app.hyperliquid.xyz/API](https://app.hyperliquid.xyz/API) にアクセスしてAPIウォレットを生成します。

`HYPERLIQUID_ACCOUNT_ADDRESS` にはメインウォレットのアドレスを、`HYPERLIQUID_PRIVATE_KEY` にはAPIウォレットのPrivate keyを設定します。APIウォレットは署名専用のため、資金を転送する必要はありません。

## 使い方

### 🐳 Docker での使い方（推奨）

#### 事前準備
```bash
# 環境変数ファイルを作成
cp .env.example .env
# .envファイルを編集してAPIキーを設定
```

#### 基本的な使い方
```bash
# 最新安定版を使用
docker run --env-file .env ghcr.io/keitaj/hyperliquid-bot:latest

# 特定の戦略とパラメーターで実行
docker run --env-file .env ghcr.io/keitaj/hyperliquid-bot:latest \
  python3 bot.py --strategy rsi --rsi-period 21 --oversold-threshold 25

# 取引対象通貨を指定して実行
docker run --env-file .env ghcr.io/keitaj/hyperliquid-bot:latest \
  python3 bot.py --strategy macd --coins BTC ETH

# デーモン実行（バックグラウンドで継続動作）
docker run -d --name hyperliquid-bot --env-file .env \
  -v $(pwd)/logs:/app/logs ghcr.io/keitaj/hyperliquid-bot:latest
docker logs -f hyperliquid-bot

# 残高確認
docker run --rm --env-file .env ghcr.io/keitaj/hyperliquid-bot:latest \
  python3 check_balance.py
```

#### 利用可能なイメージタグ
- `latest` - 最新安定版
- `v0.3.0` - 特定バージョン

### 🐍 Python での使い方

#### 依存関係のインストール
```bash
pip3 install .
```

#### 基本的な使い方
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

#### パラメーターのカスタマイズ

**共通パラメーター**
```bash
# ポジションサイズと損益設定を変更
python3 bot.py --position-size-usd 200 --take-profit-percent 10 --stop-loss-percent 3
```

**戦略別パラメーター**
```bash
# Simple MA戦略
python3 bot.py --strategy simple_ma --fast-ma-period 5 --slow-ma-period 20

# RSI戦略
python3 bot.py --strategy rsi --rsi-period 21 --oversold-threshold 25 --overbought-threshold 75

# Bollinger Bands戦略
python3 bot.py --strategy bollinger_bands --bb-period 25 --std-dev 2.5

# MACD戦略
python3 bot.py --strategy macd --fast-ema 10 --slow-ema 20 --signal-ema 7

# Grid Trading戦略
python3 bot.py --strategy grid_trading --grid-levels 15 --grid-spacing-pct 0.3 --position-size-per-grid 30

# Breakout戦略
python3 bot.py --strategy breakout --lookback-period 30 --volume-multiplier 2.0 --atr-period 20

# Market Making戦略
python3 bot.py --strategy market_making --spread-bps 10 --order-size-usd 100 --maker-only --taker-fallback-age 60
```

**リスクガードレールパラメーター**
```bash
# リスク制限の設定
python3 bot.py --strategy rsi \
  --max-position-pct 0.1 \
  --max-margin-usage 0.7 \
  --daily-loss-limit 500 \
  --per-trade-stop-loss 0.05 \
  --max-open-positions 3 \
  --risk-level yellow
```

#### 残高・ポジション確認
```bash
python3 check_balance.py
```

実行例:
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

## HIP-3 マルチDEX取引

[HIP-3](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals) は、Hyperliquid L1上にビルダーが独自のPerpsDEXをデプロイできる仕様です。すべてのHIP-3 DEXは同じHyperliquid APIを共有しており、上場銘柄とオラクル設定が異なるだけです。

### 対応プラットフォーム

| プラットフォーム | DEX名 | 取引対象 |
|---|---|---|
| 標準 Hyperliquid | (なし) | 暗号資産Perps（BTC、ETH、SOLなど） |
| [trade.xyz](https://trade.xyz) | `xyz` | 株式・コモディティPerps（AAPL、GOLD、CLなど） |
| [Felix](https://trade.usefelix.xyz) | `flx` | 株式・コモディティPerps |
| [Markets by Kinetiq](https://markets.xyz) | `km` | 各種（担保: USDH） |
| [Based](https://basedapp.xyz) | (なし) | 標準HLのフロントエンド — `ENABLE_STANDARD_HL=true` で対応 |
| [Ventuals](https://app.ventuals.com) | `vntl` | — |
| [HyENA](https://app.hyena.trade) | `hyna` | — |
| [dreamcash](https://trade.dreamcash.xyz) | `cash` | — |

> **注意**: DEX名はオンチェーンで割り当てられます。現在の完全なリストはHyperliquid APIの `{"type": "perpDexs"}` で確認できます。

### 設定

`.env`ファイルに以下を追加します：

```bash
# 取引対象のHIP-3 DEX名（カンマ区切り）
TRADING_DEXES=xyz,flx

# falseにするとHIP-3 DEXのみで取引（標準HL Perpsを無効化）
ENABLE_STANDARD_HL=true

# DEXごとの取引通貨リスト（省略時はそのDEXの全通貨）
XYZ_COINS=XYZ100,XYZ200
FLX_COINS=NVDA,AAPL,WTI
```

### HIP-3 コマンドラインオプション

```bash
# trade.xyzのみでRSI戦略を実行
python3 bot.py --strategy rsi --dex xyz --no-hl

# Felix株式Perps（NVDA、AAPL）と標準HL（BTC/ETH）を同時に取引
FLX_COINS=NVDA,AAPL python3 bot.py --strategy simple_ma --coins BTC ETH --dex flx

# .envに設定した全DEXで取引
python3 bot.py --strategy macd
```

| オプション | 説明 |
|---|---|
| `--dex DEX [DEX ...]` | 取引するHIP-3 DEX名（`TRADING_DEXES`環境変数を上書き） |
| `--no-hl` | 標準Hyperliquid Perpsを無効化し、HIP-3 DEXのみで取引 |

### HIP-3の仕組み

HIP-3資産には専用の整数アセットIDが使用されます：

```
asset_id = 100000 + (perp_dex_index × 10000) + index_in_meta
```

例：`xyz`が2番目のDEX（index=1）で、`XYZ100`がその最初の資産（index=0）の場合：
```
asset_id = 100000 + (1 × 10000) + 0 = 110000
```

ボットは起動時に以下を自動処理します：
1. `perpDexs` APIで全登録DEXとそのインデックスを取得
2. 設定された各DEXの `meta` を取得して資産リストを把握
3. アセットIDを計算しSDKのルックアップテーブルに注入
4. HIP-3コインを `"dex:coin"` 形式（例：`"xyz:XYZ100"`、`"flx:NVDA"`）で統一管理

---

## 機能

- **Market Data**: リアルタイム価格、オーダーブック、ローソク足データの取得
- **Order Management**: 指値注文、成行注文の発注とキャンセル
- **Risk Management**: レバレッジ制限、最大ドローダウン、日次損失制限
- **Multiple Strategies**: 7つの異なる取引戦略から選択可能
- **Risk Guardrails**: マージン制限、日次損失制限、トレードごとのストップロス、動的リスクレベルの設定
- **HIP-3 Multi-DEX**: Hyperliquid、trade.xyz、Felix等のHIP-3 DEXを横断して同時取引

## 取引戦略

| # | 戦略 | 説明 |
|---|---|---|
| 1 | `simple_ma` | 移動平均クロスオーバー — ゴールデンクロスで買い、デッドクロスで売り |
| 2 | `rsi` | RSI売られすぎ/買われすぎ — RSI < 30で買い、RSI > 70で売り |
| 3 | `bollinger_bands` | ボリンジャーバンド反発とボラティリティブレイクアウト |
| 4 | `macd` | MACD/シグナルクロスオーバーとダイバージェンス検出 |
| 5 | `grid_trading` | レンジ相場で一定間隔のグリッド注文 |
| 6 | `breakout` | 出来高・ATR確認付きサポート/レジスタンスブレイクアウト |
| 7 | `market_making` | ミッドプライス周辺の対称的な買い/売り指値でスプレッド獲得 |

全パラメータはCLIフラグで設定可能です。
`python3 bot.py --help` で全一覧を確認するか、下記の[パラメータリファレンス](#パラメータリファレンスai向け)を参照してください。

## リスクガードレール

環境変数またはCLIフラグで設定可能なリスク管理パラメータ（CLIが優先）。

| 環境変数 | CLIフラグ | デフォルト | 説明 |
|---|---|---|---|
| `MAX_POSITION_PCT` | `--max-position-pct` | 0.2 | アカウントに対する最大ポジション割合 |
| `MAX_MARGIN_USAGE` | `--max-margin-usage` | 0.8 | この比率以上で新規注文を停止 |
| `FORCE_CLOSE_MARGIN` | `--force-close-margin` | — | この比率以上で全ポジションを強制決済 |
| `DAILY_LOSS_LIMIT` | `--daily-loss-limit` | — | 日次損失がこの金額（$）を超えるとボットを停止 |
| `PER_TRADE_STOP_LOSS` | `--per-trade-stop-loss` | — | この損失%で個別トレードを決済（例: 0.05 = 5%） |
| `MAX_OPEN_POSITIONS` | `--max-open-positions` | 5 | 最大同時ポジション数 |
| `COOLDOWN_AFTER_STOP` | `--cooldown-after-stop` | 3600 | 緊急停止後の待機秒数 |
| `RISK_LEVEL` | `--risk-level` | green | `green`（100%）、`yellow`（50%）、`red`（一時停止）、`black`（全決済） |
| `METRICS_CACHE_TTL` | — | 2.0 | リスクメトリクスのキャッシュ秒数（6銘柄以上の場合は10以上を推奨） |

### レートリミッター

Hyperliquidは1,200 weight/分（〜20 req/秒）を許可しています。環境変数でレートリミッターを設定可能です：

| 環境変数 | デフォルト | 説明 |
|---|---|---|
| `RATE_LIMIT_RPS` | 5.0 | 秒あたりリクエスト数（最大20） |
| `RATE_LIMIT_BURST` | 8 | バースト上限（最大20） |
| `RATE_LIMIT_BACKOFF` | 2.0 | レート制限エラー時のバックオフ倍率 |
| `RATE_LIMIT_MAX_BACKOFF` | 30.0 | 最大バックオフ秒数 |

## 技術ドキュメント

より詳細な技術情報については、以下のドキュメントを参照してください：

- [タイムフレームとパラメータの詳細](./docs/technical-notes/timeframes.md) - 各戦略のタイムフレームとパラメータ単位の説明
- [Docker リリースプロセス](./docs/docker-release.md) - Dockerイメージの自動リリースについて

## ファイル構成

- `bot.py`: メインのボットクラス
- `config.py`: 設定管理
- `market_data.py`: マーケットデータの取得
- `order_manager.py`: 注文管理
- `risk_manager.py`: リスク管理
- `rate_limiter.py`: APIレート制限
- `hip3/`: HIP-3マルチDEXサポート
  - `dex_registry.py`: DEX探索・アセットID解決
  - `multi_dex_market_data.py`: DEX対応マーケットデータ管理
  - `multi_dex_order_manager.py`: DEX対応注文管理
- `strategies/`: 取引戦略
  - `base_strategy.py`: 戦略の基底クラス
  - `simple_ma_strategy.py`: 移動平均戦略
  - `rsi_strategy.py`: RSI戦略
  - `bollinger_bands_strategy.py`: ボリンジャーバンド戦略
  - `macd_strategy.py`: MACD戦略
  - `grid_trading_strategy.py`: グリッド取引戦略
  - `breakout_strategy.py`: ブレイクアウト戦略
  - `market_making_strategy.py`: マーケットメイキング戦略
- `validation/`: 事前バリデーション
  - `margin_validator.py`: マージン・設定バリデーション
- `docs/`: ドキュメント
  - `technical-notes/`: 技術的な詳細ドキュメント

## 注意事項

- 本番環境で使用する前に、必ずテストネットで動作確認してください
- 秘密鍵は安全に管理してください
- リスク管理パラメータは慎重に設定してください
- HIP-3 DEXは標準Hyperliquidより手数料が高い場合があります（通常2倍、50%がDEXデプロイヤーに配分）
- HIP-3 DEXは現在、アイソレートマージンのみ対応（クロスマージン未対応）

---

## パラメータリファレンス（AI向け）

> このセクションは機械可読形式です。全CLIフラグ、configキー、戦略ごとのデフォルト値を構造化YAMLで記載しています。CLIフラグは `--kebab-case`、config dictキーは `snake_case`。設定マージ: `default_configs[strategy]` がベース、CLI指定値で上書き。

詳細は [English README - Parameter Reference](README.md#parameter-reference-for-ai-agents) を参照してください。
