#!/usr/bin/env zsh
# ==============================================================
# run_dev.sh - GRACE-Support 開発サーバ（backend + frontend）一括起動
# ==============================================================
# 使用法:
#   chmod +x run_dev.sh
#   ./run_dev.sh
#
#   Ctrl+C で backend / frontend の両方を停止します。
#
# 前提条件:
#   - リポジトリルートの .env に ANTHROPIC_API_KEY（LLM）と
#     GOOGLE_API_KEY（Embedding）が設定済み
#   - Qdrant が起動済み（別実行）:
#       docker-compose -f docker-compose/docker-compose.yml up -d
#   - uv / Node.js（npm）が導入済み
#
# このスクリプトがやること:
#   1. uv sync --extra dev（バックエンド依存）
#   2. frontend の依存を用意（node_modules が無ければ npm install）
#   3. FastAPI（uvicorn, :8000）と Vite（React, :5173）を同時起動
#
# 起動後のアクセス先:
#   - UI : http://localhost:5173  ← ブラウザで開くのはこちら
#   - API: http://localhost:8000  （/docs で自動ドキュメント）
# ==============================================================
set -e
set -u

# スクリプトの場所（＝リポジトリルート）へ移動
cd "$(dirname "$0")"

BACKEND_PORT="${BACKEND_PORT:-8000}"

# --- 依存の用意 --------------------------------------------------
echo "==> [1/3] uv sync --extra dev（バックエンド依存）"
uv sync --extra dev

echo "==> [2/3] frontend 依存の確認"
if [ ! -d frontend/node_modules ]; then
  echo "    node_modules が無いため npm install を実行します"
  (cd frontend && npm install)
else
  echo "    node_modules 済み（スキップ。再インストールは 'cd frontend && npm install'）"
fi

# --- Qdrant 疎通チェック（起動していなくても続行） ----------------
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
if command -v curl >/dev/null 2>&1; then
  if ! curl -sf "${QDRANT_URL}/healthz" >/dev/null 2>&1; then
    echo "⚠️  Qdrant (${QDRANT_URL}) に接続できません。別ターミナルで起動してください:"
    echo "      docker-compose -f docker-compose/docker-compose.yml up -d"
  fi
fi

# --- 終了時に両プロセスを停止 ------------------------------------
BACK_PID=""
FRONT_PID=""
cleanup() {
  echo ""
  echo "==> 停止処理中..."
  [ -n "${FRONT_PID}" ] && kill "${FRONT_PID}" 2>/dev/null || true
  [ -n "${BACK_PID}" ] && kill "${BACK_PID}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- 起動 --------------------------------------------------------
echo "==> [3/3] 開発サーバを起動します（停止は Ctrl+C）"
echo "    backend : http://localhost:${BACKEND_PORT}  (docs: /docs)"
echo "    frontend: http://localhost:5173  ← ブラウザで開くのはこちら"

uv run uvicorn backend.app.main:app --reload --port "${BACKEND_PORT}" &
BACK_PID=$!

(cd frontend && npm run dev) &
FRONT_PID=$!

# どちらかが終了するまで待つ（Ctrl+C で cleanup が走る）
wait
