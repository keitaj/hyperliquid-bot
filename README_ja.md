# Hyperliquid Trading Bot

[![Test](https://github.com/keitaj/hyperliquid-bot/actions/workflows/test.yml/badge.svg)](https://github.com/keitaj/hyperliquid-bot/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Release](https://img.shields.io/github/v/release/keitaj/hyperliquid-bot)](https://github.com/keitaj/hyperliquid-bot/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

[English](README.md) | **日本語**

Hyperliquid DEX用の自動取引ボットです。**HIP-3マルチDEX対応**（trade.xyzなどのビルダー・デプロイ型DEX）。

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

# Market Making戦略（BBOモード — best bid/askに注文を置く）
python3 bot.py --strategy market_making --bbo-mode --bbo-offset-bps 0.5
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

## HIP-3 マルチDEX取引

[HIP-3](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals) は、Hyperliquid L1上にビルダーが独自のPerpsDEXをデプロイできる仕様です。すべてのHIP-3 DEXは同じHyperliquid APIを共有しており、上場銘柄とオラクル設定が異なるだけです。

### 対応プラットフォーム

| プラットフォーム | DEX名 | 取引対象 |
|---|---|---|
| 標準 Hyperliquid | (なし) | 暗号資産Perps（BTC、ETH、SOLなど） |
| [trade.xyz](https://trade.xyz) | `xyz` | 株式・コモディティPerps（AAPL、GOLD、CLなど） |
| [Entropy](https://entropy.io) | `io` | プレIPO・株式Perps（ANTH、OAI、IONQなど） |

> **注意**: DEX名はオンチェーンで割り当てられます。現在の完全なリストはHyperliquid APIの `{"type": "perpDexs"}` で確認できます。新しいDEXはデプロイヤーのローンチに伴いこのリストに追加されます。

> **⚠️ 撤退済みDEX**: 2026年中に複数の初期HIP-3デプロイヤーが撤退しました: Felix（`flx`）、Ventuals（`vntl`）、HyENA（`hyna`）、dreamcash（`cash`）、Markets by Kinetiq（`km` と後継の `mkts`）。これらは `TRADING_DEXES` から削除してください — DEX登録自体はオンチェーンに残っていますが、マーケットは実質すべて上場廃止（delisted）のため、起動時に各コインが除外され（`[coin-skip] <coin> reason=delisted`）、そのDEXからは何も取引されません（`DEXes not found on-chain` 警告は未登録の名前、つまりタイポの場合にのみ出ます）。

### 設定

`.env`ファイルに以下を追加します：

```bash
# 取引対象のHIP-3 DEX名（カンマ区切り）
TRADING_DEXES=xyz

# falseにするとHIP-3 DEXのみで取引（標準HL Perpsを無効化）
ENABLE_STANDARD_HL=true

# DEXごとの取引通貨リスト（省略時はそのDEXの全通貨）
XYZ_COINS=AAPL,NVDA,SP500

# DEXごとのビルダーフィー（任意 — 一部HIP-3デプロイヤーの
# リワードプログラムで必要）。BUILDER_FEES_<DEX>_ADDRESS を設定すると、
# そのDEXの全注文にビルダーコードが付与され、起動時に事前承認されます。
# 未設定なら従来どおりビルダーなしの挙動です。
# BUILDER_FEES_<DEX>_TENTHS_BPS は注文ごとのビルダーフィー
# （0.1bps単位。10 = 1 bp = 注文金額の0.01%、デフォルト10）。
# BUILDER_FEES_<DEX>_MAX_FEE_RATE は事前承認の上限で、
# 注文ごとのフィー以上でなければなりません（下回ると全注文が拒否されます）。
# デフォルトは0.05%で、f=50までの余裕があります。
# デプロイヤーがビルダーコードを公開しているDEXの設定例:
# BUILDER_FEES_CASH_ADDRESS=0xabc...
# BUILDER_FEES_CASH_TENTHS_BPS=10        # 注文ごとに1 bp
# BUILDER_FEES_CASH_MAX_FEE_RATE=0.05%   # 上限5 bp
```

### HIP-3 コマンドラインオプション

```bash
# trade.xyzのみでRSI戦略を実行
python3 bot.py --strategy rsi --dex xyz --no-hl

# trade.xyz株式Perps（NVDA、AAPL）と標準HL（BTC/ETH）を同時に取引
XYZ_COINS=NVDA,AAPL python3 bot.py --strategy simple_ma --coins BTC ETH --dex xyz

# .envに設定した全DEXで取引
python3 bot.py --strategy macd
```

| オプション | 説明 |
|---|---|
| `--dex DEX [DEX ...]` | 取引するHIP-3 DEX名（`TRADING_DEXES`環境変数を上書き） |
| `--no-hl` | 標準Hyperliquid Perpsを無効化し、HIP-3 DEXのみで取引 |
| `--enable-ws` | リアルタイムL2ブック更新のWebSocketフィードを有効化（REST API呼び出しを削減） |

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
4. HIP-3コインを `"dex:coin"` 形式（例：`"xyz:AAPL"`、`"xyz:NVDA"`）で統一管理

---

## 機能

- **Market Data**: リアルタイム価格、オーダーブック、ローソク足データの取得
- **Order Management**: 指値注文、成行注文の発注とキャンセル
- **Risk Management**: レバレッジ制限、最大ドローダウン、日次損失制限
- **Multiple Strategies**: 7つの異なる取引戦略から選択可能
- **Risk Guardrails**: マージン制限、日次損失制限、トレードごとのストップロス、動的リスクレベルの設定
- **Startup Validation**: 起動時に戦略パラメータを検証し、全エラーを一括レポート
- **Structured Logging**: ログ集約ツール向けのJSON出力モード（`LOG_FORMAT=json`）
- **HIP-3 Multi-DEX**: 標準Hyperliquidとtrade.xyz等のHIP-3 DEXを横断して同時取引

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

### JSON設定レイヤー

`--config <path.json>` と `$BOT_CONFIG=<path.json>`（環境変数フォールバック）の両方が使えます。`--config` は繰り返し指定して複数ファイルをレイヤー化できます（後のファイルが先のファイルを上書き）。優先順位は **CLI > 環境変数 > JSON > dataclassデフォルト** — JSONは既存のフラットなキー名前空間の上に載る、読みやすい表層フォーマットという位置づけです。

JSONはフラット形式・ネスト形式のどちらでも（混在でも）書けます。ネスト形式は既知の名前空間（`market_making`、`risk`）配下でパスをアンダースコア連結して自動フラット化されます：

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

は次のようにフラット化されます：

```python
{"spread_bps": 10, "refresh_tolerance_bp": 1, "refresh_max_age_seconds": 240,
 "forager_enabled": True, "forager_score_threshold": 30.0, "daily_loss_limit": 200}
```

未知のキーは警告（タイポ検出）を出しつつバリデーターに渡され、中断するかどうかはバリデーターが判断します。存在しないファイルは警告のうえスキップされ（`--config /missing.json` のようなタイポで起動がブロックされません）、不正なJSONは終了コード2で中断します。完全なテンプレートは `examples/config.example.json` を参照してください。

`risk` 名前空間はボットの `Config` クラス（既存の `--max-position-pct` / `--daily-loss-limit` 等のCLIフラグと同じ経路）に流れ、`strategy_config` には入りません。個々のリスクパラメータはCLIフラグがJSONより優先されるため、JSONにデフォルトを置いてコマンドラインから実行ごとに上書きする運用ができます。

### Market Making戦略の機能詳細

`market_making` 戦略は**段階的クローズ価格**を採用しています: ポジションの経過時間に応じて、テイクプロフィット価格をフルスプレッド → ブレイクイーブン（max ageの50%時点）→ 小さな損失許容（75%時点）へと引き下げ、コストの高いテイカー強制決済を減らします。損失許容幅は `--aggressive-loss-bps`（デフォルト: 1 bps）で設定します。強制決済フェーズでは、`--force-close-max-loss-bps` により `aggressive-loss-bps` から設定上限まで、テイカー期限に近づくにつれて損失許容を段階的に広げられます。**含み損アーリークローズ**（`--unrealized-loss-close-bps`）: ポジションの含み損がこの閾値（bps）を超えると、経過時間に関係なく即座にテイカー注文でクローズします。経過時間ベースのクローズが発動する前に大きな逆行をキャップします。デフォルト: 0（無効）。

**コイン別クローズ段階**（`--coin-close-tier-overrides`）: ブレイクイーブン/アグレッシブ段階への移行割合をコイン別に上書きします。例: `"NVDA:0.30/0.55,xyz:XYZ100:0.40/0.65"`（形式は `COIN:BREAKEVEN/AGGRESSIVE`、`0 <= breakeven < aggressive <= 1` が必要）。逆選択の激しいコインは早めにブレイクイーブンへ譲歩させ（テイカー強制決済前にメイカーで抜ける確率を上げる）、落ち着いたコインは広いテイクプロフィット枠を維持できます（グローバルデフォルトより大きい値は緩和方向に働きます）。未指定のコインは `--close-breakeven-pct` / `--close-aggressive-pct` を使います。**トキシシティ連動の段階加速**（`--close-tier-toxicity`）: AdverseSelectionTrackerの直近ウィンドウ平均markoutが `--close-tier-toxicity-threshold-bps`（デフォルト -2.0）以下の間、コインの段階割合に `--close-tier-toxicity-multiplier`（デフォルト 0.6）を動的に乗算します。`--enable-adverse-selection-log` とWebSocketが必要で、トラッカーやサンプルが利用できない間はベース挙動にフォールバックします。`--close-tier-toxicity-floor-pct`（割合の下限）と `--close-tier-min-seconds`（ブレイクイーブン移行の絶対下限。dynamic position ageと併用する場合に推奨）で境界を設定します。どちらの機能も強制決済・テイカーフォールバックのタイミング自体は変えません。

**BBOモード**（`--bbo-mode`）: `mid ± spread_bps` ではなくbest bid/askに注文を置きます。Hyperliquidの市場スプレッドは通常0.1〜2 bpsのため、`SPREAD_BPS=5` でもBBOから4〜5 bps離れてしまい約定率が下がります。BBOモードは現在のベスト価格を追跡することで約定率を改善します。`--bbo-offset-bps N` でBBOからN bps後ろに置けます（デフォルト: 0 = BBOと同値）。BBOが取得できないときは `mid ± spread_bps` にフォールバックします。

**リフレッシュ許容幅**（`--refresh-tolerance-bp`）: 記録済みの注文価格と現在の理想価格のドリフトがこのbps以内なら、サイクルをまたいで既存注文を維持します。不要なキャンセル/再発注を減らし、静かな相場でHyperliquidのprice-timeマッチングにおけるキュー優先順位を保ちます。ドリフトが許容幅を超えれば即座にキャンセルされます。これとは独立に `--refresh-max-age-seconds`（デフォルト: `refresh_interval_seconds * 4`）が注文年齢のセーフティネット上限として働きます。デフォルト: `0`（無効。完全な後方互換として年齢ベースのみの挙動を維持）。

**コイン別オーバーライド**（`--coin-offset-overrides`、`--coin-spread-overrides`、`--coin-size-overrides`、`--coin-unrealized-loss-overrides`）: BBOオフセット、スプレッド、注文サイズ、含み損アーリークローズ閾値をコイン別に上書きします。形式: `"SP500:0.5,MSFT:3"`。素の名前もDEXプレフィックス付き（`xyz:SP500:0.5`）も使えます。未指定のコインはグローバルデフォルトを使います。`--coin-size-overrides` はコイン別の注文サイズUSDを指定し（例: `"TSLA:150,NVDA:150"`）、`0` を設定するとそのコインの注文をスキップします。`--coin-unrealized-loss-overrides` は低ボラコインで閾値を緩め（`"INTC:25"`）、高ボラコインで締める（`"OIL:10"`）のに使え、`0` でそのコインの含み損機能を無効化します。

**クワイエットアワー**（`--quiet-hours-utc`）: 指定したUTC時間帯に発注を停止、またはスプレッドを拡大します（例: `"17"` や `"17,18"`）。デフォルトは完全停止。`--quiet-hours-spread-multiplier N` を指定するとスプレッドをN倍に広げる動作に変わります。クワイエットアワー中もポジション管理は継続されます。

**ドレインモード**（`--drain-flag-file`）: グレースフルなシャットダウン前処理に使うフラグファイルのパスです。ファイルが存在する間、戦略は新規エントリー注文を止め、既存ポジションの管理（通常のメイカー優先クローズフロー）のみ行います。SIGTERMを送る前に外部スクリプト（セッション切り替えヘルパーなど）からトリガーする想定で、テイカーIOCクローズではなくメイカークローズでポジションを巻き取れます。クワイエットアワーと重なった場合はドレインが優先されます。空/未設定 = 無効。

**スプレッドスケジュール**（`--spread-schedule`）: 時間帯別のスプレッド倍率です。形式: `"HOUR:MULT,..."` またはレンジ指定 `"START-END:MULT,..."`（例: `"0-3:1.5,14:1.5,20:1.5"`）。レンジは深夜をまたげます（`"22-2:1.5"` は22,23,0,1,2時をカバー）。スケジュールにない時間は倍率1.0（変更なし）。倍率0は完全停止モード（クワイエットアワーと同じ）です。クワイエットアワーと共存でき、クワイエットアワーの完全停止が優先、それ以外は倍率が重ねがけされます。

**マーケットカレンダー・フェーズスケジュール**（`--coin-market-overrides` + `--phase-spread-multipliers`）: UTC固定のスプレッドスケジュールに代わる、コイン別・タイムゾーン対応の仕組みです。原資産が異なる国で取引される銘柄向けです。コインを組み込みのマーケットカレンダーに割り当て（`"NVDA:NYSE,SP500:CME_INDEX,SKHY:KRX"`、利用可能ID: `NYSE`、`CME_INDEX`、`KRX`、`TSE`、`HKEX`、`FX`、`NONE`）、セッションフェーズごとに倍率を設定します（`"open_window:3.0,close_window:3.0,closed:2.0,lunch:0,regular:1.0"`）。フェーズは `closed` / `open_window`（各オープン後の最初の `phase_open_window_minutes` 分、デフォルト30）/ `regular` / `lunch` / `close_window`（各クローズ前の `phase_close_window_minutes` 分）。セッション時刻は各市場自身のタイムゾーン（`zoneinfo`）で保持されるため、夏時間の切り替えに設定変更は不要です。割り当てられたコインではフェーズ倍率が `spread_schedule` と `quiet_hours_utc` を**置き換え**、未割り当てのコインは従来の挙動のままです。倍率0はそのコインの発注を停止しますが、ポジション管理は継続します（クワイエットアワーと同じセマンティクス）。未知のマーケットIDは警告ログを出して従来スケジュールにフォールバックし、起動をブロックしません。取引所の祝日は対象外です — 実際の休場検出にはオラクル・ステイルネスガードと組み合わせてください。

**連敗クールダウン**（`--loss-streak-limit`）: 連続してこの回数負けクローズしたコインを `--loss-streak-cooldown` 秒（デフォルト300）停止し、その後自動再開します。コイン別クールダウンマップを `--auto-exclude` および `--forager` と共有します。デフォルト0（無効）。

**逆選択による自動除外**（`--auto-exclude`）: AdverseSelectionTrackerが中程度の逆選択（`avg_<window>` が `--auto-exclude-threshold-bps`、デフォルト `-3.0` 未満）を `--auto-exclude-consecutive` 回のサマリーウィンドウ連続（デフォルト3回、デフォルトの300秒ログ間隔で約15分）で報告したコインを自動停止します。コインは `--auto-exclude-cooldown` 秒（デフォルト1800）停止後、自動再開します。`--enable-adverse-selection-log` が必要です。ウィンドウごとの `min_fills` フィルタで低出来高ノイズによる誤発動を防ぎます。コイン別クールダウンマップを `--loss-streak-limit` と共有するため、両機能は自然に組み合わせられます。

**コイン別ポジションキャップ**（`max_position_multiple`、環境変数のみ）: 累積ポジション額（`|position| × mid_price`）がそのコインの `max_position_multiple × effective_order_size_usd` に達すると、同方向のエントリーを抑止します。ポジションを積み上げて最後に一括クローズするシナリオがマーケットメイキングボットを過大な逆行約定に晒すのを防ぐ目的です。キャップは `coin_size_overrides` を尊重するため、コイン別注文サイズの上書きに比例してキャップも増減します。デフォルト `0.0`（無効）はキャップ導入前の挙動を維持します。**⚠️ 現在の片面フローではno-op** — 下記の[インベントリスキューとポジションキャップは不活性](#インベントリスキューとポジションキャップは不活性)を参照。

#### インベントリスキューとポジションキャップは不活性

`inventory_skew_bps` と `max_position_multiple` は**現状no-op**です: マーケットメイキングのループはポジションを持つコインをポジションクローザーに引き渡して発注をスキップするため、注文の配置はフラットな間しか実行されません。どちらの機能もポジションが非ゼロのときに発動する設計のため、配置される注文に影響を与えられません。非ゼロ値を設定すると起動時に警告が出ます。

両者が削除されず残っているのは、双方向クオートモードでは意味を持つためです — 在庫を持ったままクオートを続けるモードなら、`inventory_skew_bps` は在庫を平均回帰させる方向にクオートをシフトし、`max_position_multiple` は積み上がりを制限します。そのモードの実装は設定トグルではなく明確な戦略変更です: 在庫の早期フラット化（約定後markoutを穏やかに保つ）とクオート稼働時間のトレードオフであり、リスクマネージャーのネット在庫上限が注文サイズに対して十分大きいときにのみ利益になります。

**フィル特徴量ロギング**（`--fill-feature-log`）: 約定ごとに1行のJSONを日次ローテーションファイル（`{dir}/YYYYMMDD.jsonl`、UTC）へ書き出します。内容は約定の結合キー（`tid`/`oid`/`hash`）、約定属性、約定時点のオーダーブック特徴量（スプレッド、ブックインバランス、マイクロプライススキュー、トップオブブックサイズ、直近実現ボラティリティ）、およびラベルとしてトラッカーの5s/30s/60s markoutサンプル — オフラインの逆選択モデリングにそのまま使える教師ありデータセットです。観測専用: WebSocketスレッドはファイルIOを行わず（レコードはメモリにバッファされ、markoutラベルを埋め込めるよう65秒の成熟ウィンドウ後にメインループからフラッシュ）、全ての失敗は握りつぶされ、日次サイズ上限とバッファ上限がディスクとメモリを保護します。`--enable-ws` と `--enable-adverse-selection-log` が必要です。特徴量定義は単一の共有関数（`fill_features.compute_fill_features`）にあり、将来のボット内推論が同一の入力を使えます（train/serveスキューなし）。デフォルト無効。

**Forager: 複合スコアによる自動除外**（`--forager`）: `--auto-exclude`（markoutベース）を補完し、各コインを3つの独立した次元でスコアリングして複合スコアが低い状態が続いたら停止します。次元は**アクティビティ**（約定の新しさ。数時間約定がないコインのような死んだ市場を検出）、**クローズ品質**（メイカークローズ率。markoutが中立に見えても構造的にテイカーフォールバックするコインを検出）、**コスト**（直近の$/1K vol。緩やかな出血を検出）。重み付き複合スコア `[0, 100]` を毎サイクル計算し、`--forager-threshold`（デフォルト30）未満が `--forager-consecutive` 回（デフォルト3）続くと、共有クールダウンマップに `--forager-cooldown` 秒（デフォルト1800）を設定します。重みは `--forager-w-activity`、`--forager-w-quality`、`--forager-w-cost` で設定可能。内部の計算定数（ウィンドウ長、アイドル猶予、コストスケール、最小クローズ数ゲート）は環境変数（`FORAGER_WINDOW_SECONDS`、`FORAGER_ACTIVITY_IDLE_MIN_SECONDS`、`FORAGER_COST_MAX_PER_1K`、`FORAGER_MIN_CLOSES_FOR_QUALITY`、`FORAGER_CHECK_INTERVAL_SECONDS`）で上書きできます。デフォルト無効。Foragerと `--auto-exclude` は併用でき、どちらもクールダウンを設定できます。

**オーダーブック・インバランスフィルタ**（`--imbalance-threshold`）: 発注時点でL2ブックのインバランスが閾値（0〜1。例: 0.4は約70%/30%の偏りでスキップ）を超えている側のクオートをスキップします。WebSocketフィード経由で既存注文をキャンセルする下記の `--imbalance-guard-threshold` とは別物です。デフォルト0（無効）。

**WebSocketガード**（`--enable-ws` が必要）:
- `--bbo-guard-threshold-bps`: BBOが動いたとき古いエントリー注文をキャンセル（デフォルト: 2.0）
- `--imbalance-guard-threshold`: L2ブックが偏ったとき片側をキャンセル（0〜1、デフォルト: 0）
- `--close-refresh-threshold-bps`: BBO変化時にクローズ注文をリフレッシュしメイカー約定率を改善（デフォルト: 0 = 無効）

**最大逆行幅（MAE）ロギング**（`track_max_adverse_excursion`、デフォルト有効）: ポジションの保有中に到達した最悪の含み損を記録し、`[close-reason]` ログ行に `mae_bps`（正 = 逆行）と `mae_age`（最悪時点のポジション年齢）を追記します。利益が出たメイカークローズを含む全クローズ理由で出力されます。一度逆行してから回復したポジションこそ含み損閾値が早期にクローズしてしまうものであり、`unrealized_loss_close_bps` をキャリブレーションする際の分母になるからです（約定データだけでは見えません）。観測専用: クローズ判断には一切影響せず、サンプルはキャッシュのみのマーケットデータ読み取りから取るためAPI呼び出しは増えません。利用上の注意2点: サンプリングはメインループの1サイクルに1回のため、値は真の逆行幅の**下限**です（サイクル内のスパイクが戻った場合は観測されず、ある水準を超えたポジション数は過少カウントになります）。またフィールドは行末に追記されるため、既存の `last_tier` / `max_age` ログパーサーはそのまま動きます。

**オラクル乖離ガード**（`--oracle-guard`）: `activeAssetCtx` ストリームを購読し、HIP-3の `oraclePx` フィード（デプロイヤーが約3秒ごとにプッシュ、各更新は直前値の1%でキャップ）を、古いクオートを狙い撃ちされることへの防御シグナルとして使います。ゲートは2つ: **乖離ゲート**は `|ブックmid − oraclePx|` が `--oracle-divergence-threshold-bps` を超えたとき、古くなった側のエントリークオートをキャンセルします（オラクルがmidより上 → 置いてあるSELLが割安で危険 → SELLをキャンセル、逆も同様）。**モメンタムゲート**は、単一のオラクルステップが1%キャップ付近に張り付く（`--oracle-momentum-cap-pin-bps`）か、`--oracle-momentum-min-step-bps` 以上の同方向ステップが `--oracle-momentum-consecutive` 回続いたとき、両側をキャンセルして `--oracle-guard-block-seconds` 秒間発注をブロックします。ステイルネスTTL（`--oracle-stale-ttl-seconds`）は休場の自動検出を兼ねます: オラクル値が変化しなくなると（例: 米国時間外の株式Perps）ガードは完全に解除され、浮動するブックを乖離と誤認しません。回復時には再ベースラインし、寄り付き直後のギャップをステップとして数えません。キャンセルはエントリー注文のみが対象（クローズ注文には触れません）。データ欠損 — ctx未受信、WS切断、オラクル価格のパース不能 — の場合、ガードは不活性のままです。デフォルト無効。

**逆選択ロギング**（`--enable-adverse-selection-log`）: 各約定の5秒/30秒/60秒後のmid価格変動を計測し、300秒ごとにコイン別サマリーをログ出力します。観測専用 — 取引への影響はありません。

**注文拒否ログの集約**（`--rejection-log-level`、`--rejection-summary-interval`）: 定常的なpost-only拒否（`Post only order would have immediately matched`）はメイカー専用クオートでは想定内のリトライシグナルですが、従来はERRORでログされ、MMの規模拡大とともに本物のエラーを埋もれさせていました。戦略は各拒否をAPIエラーテキストで分類し、`--rejection-summary-interval` 秒ごと（デフォルト300、0で無効）に `[reject-summary]` のINFO行を1行追加します。拒否ごとのデフォルトログレベルは `error` のままで、従来とバイト単位で同一の `Order rejected: …` 行を出すため、既存のログスクレイパーやERRORレートアラートはそのまま機能します。`--rejection-log-level warning`（または `info`）に切り替えると、分類付きのリッチな `[reject:tag] coin — …` 形式を選んだレベルで出力します。未知の拒否テキストは常に従来形式のERRORにフォールスルーするため、フォーマット変更や新しい拒否理由は見逃されません。

**動的オフセット**（`--dynamic-offset`）: トラッカーの逆選択の深刻度に基づき、コイン別のBBOオフセットを自動調整します。逆選択が強いコインはオフセットを広げ、有利なコインは狭めます。`--enable-ws` と `--enable-adverse-selection-log` が必要です。手動の `--coin-offset-overrides` がベースラインとなり、動的調整はそこへの加算/減算として働きます。

**動的ポジション年齢**（`--dynamic-age`）: 直近のボラティリティに基づき `MAX_POSITION_AGE` をコイン別に調整します。高ボラのコインは保有時間を短くし（逆選択リスクを低減）、低ボラのコインは長くします（メイカー約定確率を改善）。`--vol-adjust` と同じmid価格履歴を使います。`--dynamic-age-baseline-vol`（bps）を「通常」ボラティリティの基準として、`--dynamic-age-min` / `--dynamic-age-max`（秒）をクランプ境界として設定します。データ不足時は固定の `--max-position-age` にフォールバックします。

全パラメータはCLIフラグで設定可能で、適切なデフォルト値を持ちます。
`python3 bot.py --help` で全一覧を確認するか、下記の[パラメータリファレンス](#パラメータリファレンスai向け)を参照してください。

## リスクガードレール

環境変数またはCLIフラグで設定可能なリスク管理パラメータ（CLIが優先）。

| 環境変数 | CLIフラグ | デフォルト | 説明 |
|---|---|---|---|
| `MAX_POSITION_PCT` | `--max-position-pct` | 0.2 | アカウントに対する最大ポジション割合 |
| `MAX_MARGIN_USAGE` | `--max-margin-usage` | 0.8 | この比率以上で新規注文を停止 |
| `FORCE_CLOSE_MARGIN` | `--force-close-margin` | — | この比率以上で全ポジションを強制決済 |
| `FORCE_CLOSE_LEVERAGE` | `--force-close-leverage` | — | このレバレッジ以上で全ポジションを強制決済 |
| `DAILY_LOSS_LIMIT` | `--daily-loss-limit` | — | 日次損失がこの金額（$）を超えるとボットを停止 |
| `PER_TRADE_STOP_LOSS` | `--per-trade-stop-loss` | — | この損失%で個別トレードを決済（例: 0.05 = 5%） |
| `MAX_OPEN_POSITIONS` | `--max-open-positions` | 5 | 最大同時ポジション数 |
| `COOLDOWN_AFTER_STOP` | `--cooldown-after-stop` | 3600 | 緊急停止後の待機秒数 |
| `RISK_LEVEL` | `--risk-level` | green | `green`（100%）、`yellow`（50%）、`red`（一時停止）、`black`（全決済） |
| `METRICS_CACHE_TTL` | — | 2.0 | リスクメトリクスのキャッシュ秒数（6銘柄以上の場合は10以上を推奨） |
| `META_CACHE_TTL` | — | 3600 | 資産メタデータ（sz_decimals）のキャッシュ秒数 |
| `MIDS_CACHE_TTL` | — | 5.0 | 注文マネージャーのmid価格キャッシュ秒数 |

### マージンバリデーション

起動時バリデーションで使用する最小注文額とマージン係数です。Hyperliquidが要件を変更した場合は環境変数で上書きしてください。

| 環境変数 | デフォルト | 説明 |
|---|---|---|
| `MIN_ORDER_VALUE_DEFAULT` | 50 | デフォルトの最小注文額（USD） |
| `MIN_ORDER_VALUE_BTC` | 100 | BTCの最小注文額 |
| `MIN_ORDER_VALUE_ETH` | 100 | ETHの最小注文額 |
| `MIN_ORDER_VALUE_{COIN}` | — | 任意のコインの最小注文額（例: `MIN_ORDER_VALUE_SOL=80`） |
| `INITIAL_MARGIN_MULTIPLIER` | 3.0 | 初期注文のマージン係数 |
| `MARGIN_SAFETY_BUFFER` | 1.5 | マージン計算のセーフティバッファ |
| `STARTUP_PRICE_RETRIES` | 3 | コイン価格の取得を「利用不可」と判定する前のリトライ回数（0 = 1回のみ） |
| `STARTUP_PRICE_RETRY_INTERVAL` | 2.0 | リトライ間隔（秒） |
| `SKIP_UNAVAILABLE_COINS` | false | trueのとき、価格が取得できないコインを起動中断ではなくそのセッションから除外します。オープンポジションがある（またはポジションを確認できない）コインは決して除外されず、コインが1つも残らない場合は起動を中断します |

上場廃止されたHIP-3コイン（DEXメタで `isDelisted`）はオーダーブックが存在しないため、これらの設定に関係なく常にバリデーション前に取引リストから除外されます。`{DEX}_COINS` で明示指定したコインは `[coin-skip] <coin> reason=delisted`、価格が取得できず除外されたコインは `[coin-skip] <coin> reason=no_price` としてログされます。

### レートリミッター

Hyperliquidは1,200 weight/分（〜20 req/秒）を許可しています。環境変数でレートリミッターを設定可能です：

| 環境変数 | デフォルト | 説明 |
|---|---|---|
| `RATE_LIMIT_RPS` | 5.0 | 秒あたりリクエスト数（最大20） |
| `RATE_LIMIT_BURST` | 8 | バースト上限（最大20） |
| `RATE_LIMIT_BACKOFF` | 2.0 | レート制限エラー時のバックオフ倍率 |
| `RATE_LIMIT_MAX_BACKOFF` | 30.0 | 最大バックオフ秒数 |

### ロギング

| 環境変数 | デフォルト | 説明 |
|---|---|---|
| `LOG_FORMAT` | `text` | ログ出力形式: `text`（人間向け）または `json`（構造化、1行1 JSONオブジェクト） |
| `LOG_LEVEL` | `INFO` | ログ詳細度: `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` |

JSONモードはログ集約ツール（Datadog、CloudWatch、Lokiなど）に便利です。例：

```bash
LOG_FORMAT=json LOG_LEVEL=DEBUG python3 bot.py --strategy rsi
```

## 技術ドキュメント

より詳細な技術情報については、以下のドキュメントを参照してください：

- [タイムフレームとパラメータの詳細](./docs/technical-notes/timeframes.md) - 各戦略のタイムフレームとパラメータ単位の説明
- [Docker リリースプロセス](./docs/docker-release.md) - Dockerイメージの自動リリースについて

## ファイル構成

- `bot.py`: メインのボットクラス
- `cli.py`: CLIフラグ定義（argparse）
- `config.py`: 設定管理
- `market_data.py`: マーケットデータの取得
- `order_manager.py`: 注文管理
- `risk_manager.py`: リスク管理
- `rate_limiter.py`: APIレート制限
- `log_config.py`: ロギング設定（text / JSON構造化出力）
- `coin_utils.py`: HIP-3コイン表記の共有ヘルパー
- `account_utils.py`: アカウント残高ヘルパー（Portfolio Margin）
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
  - `mm_order_tracker.py`: MM注文追跡・古い注文の管理
  - `mm_position_closer.py`: MMポジションクローズ・テイクプロフィット管理
- `validation/`: 事前バリデーション
  - `margin_validator.py`: マージン・設定バリデーション
  - `strategy_validator.py`: 戦略パラメータバリデーション
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

機械向けリファレンスはドリフト防止のため一元管理しています。[English README - Parameter Reference](README.md#parameter-reference-for-ai-agents) を参照してください。
