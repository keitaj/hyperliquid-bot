# Docker イメージ自動リリース

このプロジェクトでは、GitHub Actionsを使用してDockerイメージを自動的にビルド・リリースします。

## 🚀 リリースプロセス

### 1. 開発版のプッシュ
```bash
git push origin main
```
**結果**: `ghcr.io/keitaj/hyperliquid-bot:main` イメージが作成される

### 2. 安定版のリリース
```bash
# バージョンタグを作成
git tag v1.0.0
git push origin v1.0.0
```
**結果**: 以下のイメージが作成される
- `ghcr.io/keitaj/hyperliquid-bot:v1.0.0`
- `ghcr.io/keitaj/hyperliquid-bot:v1.0`
- `ghcr.io/keitaj/hyperliquid-bot:v1`
- `ghcr.io/keitaj/hyperliquid-bot:latest`

## 📦 イメージの使用方法

### 最新安定版を使用
```bash
docker pull ghcr.io/keitaj/hyperliquid-bot:latest
docker run --env-file .env ghcr.io/keitaj/hyperliquid-bot:latest
```

### 特定バージョンを使用
```bash
docker pull ghcr.io/keitaj/hyperliquid-bot:v1.0.0
docker run --env-file .env ghcr.io/keitaj/hyperliquid-bot:v1.0.0
```

### 開発版を使用（最新のmainブランチ）
```bash
docker pull ghcr.io/keitaj/hyperliquid-bot:main
docker run --env-file .env ghcr.io/keitaj/hyperliquid-bot:main
```

## 🏗️ マルチプラットフォーム対応

自動的に以下のプラットフォーム用にビルドされます：
- `linux/amd64` (Intel/AMD x64)
- `linux/arm64` (Apple Silicon M1/M2, ARM64サーバー)

## 🔍 ビルド状況の確認

1. **GitHubリポジトリ** → **Actions** タブ
2. **Packages** タブでイメージを確認

## 📋 バージョン管理のベストプラクティス

### セマンティックバージョニング
- `v1.0.0` - メジャーリリース（破壊的変更）
- `v1.1.0` - マイナーリリース（機能追加）
- `v1.1.1` - パッチリリース（バグ修正）

### リリース例
```bash
# 機能追加
git tag v1.1.0
git push origin v1.1.0

# バグ修正
git tag v1.1.1
git push origin v1.1.1

# 破壊的変更
git tag v2.0.0
git push origin v2.0.0
```

## ⚡ 自動化機能

- ✅ タグ作成時に自動ビルド
- ✅ mainブランチプッシュ時に開発版ビルド
- ✅ プルリクエスト時にテストビルド
- ✅ マルチプラットフォーム対応
- ✅ キャッシュ最適化
- ✅ セキュリティ署名付き