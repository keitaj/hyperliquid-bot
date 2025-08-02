# Hyperliquid Trading Bot

Hyperliquid DEX用の自動取引ボットです。

## セットアップ

1. 依存関係のインストール:
```bash
pip3 install -r requirements.txt
```

2. 環境変数の設定:
`.env.example`を`.env`にコピーして、必要な情報を入力:
```bash
cp .env.example .env
```

3. `.env`ファイルを編集:
- `HYPERLIQUID_ACCOUNT_ADDRESS`: あなたのウォレットアドレス
- `HYPERLIQUID_SECRET_KEY`: あなたの秘密鍵
- `USE_TESTNET`: テストネットを使用する場合は`true`

## 使い方

```bash
python3 bot.py
```

## 機能

- **Market Data**: リアルタイム価格、オーダーブック、ローソク足データの取得
- **Order Management**: 指値注文、成行注文の発注とキャンセル
- **Risk Management**: レバレッジ制限、最大ドローダウン、日次損失制限
- **Strategy**: シンプルな移動平均クロスオーバー戦略

## ファイル構成

- `bot.py`: メインのボットクラス
- `config.py`: 設定管理
- `market_data.py`: マーケットデータの取得
- `order_manager.py`: 注文管理
- `risk_manager.py`: リスク管理
- `strategies/`: 取引戦略
  - `base_strategy.py`: 戦略の基底クラス
  - `simple_ma_strategy.py`: 移動平均戦略

## 注意事項

- 本番環境で使用する前に、必ずテストネットで動作確認してください
- 秘密鍵は安全に管理してください
- リスク管理パラメータは慎重に設定してください