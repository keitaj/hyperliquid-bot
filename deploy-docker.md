# Docker デプロイメントガイド

## 1. 環境変数の設定

`.env`ファイルを作成して、秘密情報を安全に管理します：

```bash
cp .env.example .env
nano .env
```

**重要**: `.env`ファイルは絶対にGitにコミットしないでください。

## 2. Dockerイメージのビルドと実行

### 開発環境での実行

```bash
# イメージをビルドして実行
docker-compose up --build

# バックグラウンドで実行
docker-compose up -d --build

# ログを確認
docker-compose logs -f

# 停止
docker-compose down
```

### 本番環境での実行

```bash
# 本番用のイメージをビルド
docker build -t hyperliquid-bot:latest .

# コンテナを実行（環境変数ファイルを指定）
docker run -d \
  --name hyperliquid-bot \
  --env-file .env \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  hyperliquid-bot:latest

# 特定の戦略で実行
docker run -d \
  --name hyperliquid-bot \
  --env-file .env \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  hyperliquid-bot:latest \
  python3 bot.py --strategy rsi --coins BTC,ETH
```

## 3. コンテナ管理コマンド

```bash
# コンテナの状態確認
docker ps

# ログ確認
docker logs -f hyperliquid-bot

# コンテナに入る
docker exec -it hyperliquid-bot /bin/bash

# 残高確認
docker exec hyperliquid-bot python3 check_balance.py

# コンテナ再起動
docker restart hyperliquid-bot

# コンテナ停止
docker stop hyperliquid-bot

# コンテナ削除
docker rm hyperliquid-bot
```

## 4. ログ管理

ログは`./logs`ディレクトリにマウントされ、ホスト側から確認できます：

```bash
# リアルタイムログ監視
tail -f logs/bot_output.log

# ログローテーション設定（本番環境推奨）
sudo tee /etc/logrotate.d/hyperliquid-bot << EOF
/path/to/hyperliquid-bot/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
}
EOF
```

## 5. セキュリティのベストプラクティス

1. **環境変数の保護**
   - `.env`ファイルの権限を制限: `chmod 600 .env`
   - Docker Secretsの使用を検討（Swarmモード）

2. **ネットワーク分離**
   - 必要最小限のポートのみ公開
   - 専用のDockerネットワークを使用

3. **イメージの最小化**
   - マルチステージビルドの使用
   - 不要なパッケージを削除

4. **定期的な更新**
   ```bash
   # ベースイメージの更新
   docker pull python:3.11-slim
   docker-compose build --no-cache
   ```

## 6. 監視とヘルスチェック

```bash
# ヘルスチェックステータス確認
docker inspect --format='{{.State.Health.Status}}' hyperliquid-bot

# リソース使用状況
docker stats hyperliquid-bot

# 自動再起動の設定確認
docker inspect hyperliquid-bot | grep -A 5 RestartPolicy
```

## 7. トラブルシューティング

### コンテナが起動しない場合
```bash
# 詳細なログを確認
docker-compose logs --tail=50

# イメージの再ビルド
docker-compose build --no-cache
```

### 環境変数が読み込まれない場合
```bash
# 環境変数の確認
docker exec hyperliquid-bot env | grep HYPERLIQUID

# .envファイルの検証
docker-compose config
```