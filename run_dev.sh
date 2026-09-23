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
#   - ローカル LLM が起動済み（`ollama serve`）で、既定モデルを取得済み
#       ollama pull gemma4:e4b
#   - リポジトリルートの .env に GOOGLE_API_KEY（Embedding）が設定済み
#     ※ LLM はローカル実行のため API キーは不要
#   - Qdrant が起動済み（別実行）:
#       docker-compose -f docker-compose/docker-compose.yml up -d
#   - uv / Node.js（npm）が導入済み
#
# このスクリプトがやること:
#   1. uv sync --extra dev（バックエンド依存）
#   2. frontend の依存を用意（node_modules が無ければ npm install）
#   3. :8000 / :5173 を使っているプロセスがあれば停止する
#      （前回の起動の取り残しなど。停止対象はコマンド行つきで表示する）
#   4. FastAPI（uvicorn, :8000）と Vite（React, :5173）を同時起動
#
# 環境変数:
#   BACKEND_PORT=8080      バックエンドのポート（既定 8000）
#   RUN_DEV_FREE_PORTS=0   使用中ポートのプロセスを停止しない（既定 1 = 停止する）
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
# Vite の既定ポート。使用中だと Vite は黙って 5174 へずれ、案内した URL と食い違う。
FRONTEND_PORT=5173

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

# --- 使用中ポートの解放 ------------------------------------------
# 前回の起動が取り残したプロセスがポートを掴んでいると、uvicorn は
# `[Errno 48] Address already in use` で起動に失敗する。
# まず TERM で止め、3 秒待っても残るものだけ KILL（-9）する
# （uvicorn の reloader が子プロセスを片付けられるよう、最初から -9 にはしない）。
listening_pids() {
  lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null || true
}

free_port() {
  local port="$1" pid i
  [ -z "$(listening_pids "${port}")" ] && return 0
  echo "⚠️  ポート ${port} は使用中です。使っているプロセスを停止します:"
  for pid in $(listening_pids "${port}"); do
    echo "      PID ${pid}: $(ps -o command= -p "${pid}" 2>/dev/null || echo '(不明)')"
    kill "${pid}" 2>/dev/null || true
  done
  for i in 1 2 3 4 5 6; do
    [ -z "$(listening_pids "${port}")" ] && break
    sleep 0.5
  done
  for pid in $(listening_pids "${port}"); do
    echo "      PID ${pid} が終了しないため kill -9 します"
    kill -9 "${pid}" 2>/dev/null || true
  done
  sleep 0.5
  if [ -n "$(listening_pids "${port}")" ]; then
    echo "❌ ポート ${port} を解放できませんでした（権限が無いプロセスの可能性）。手動で停止してください:"
    echo "      lsof -i tcp:${port}"
    exit 1
  fi
}

if [ "${RUN_DEV_FREE_PORTS:-1}" = "1" ]; then
  if command -v lsof >/dev/null 2>&1; then
    free_port "${BACKEND_PORT}"
    free_port "${FRONTEND_PORT}"
  else
    echo "⚠️  lsof が無いため、使用中ポートの確認をスキップします"
  fi
fi

# --- 終了時に両プロセスを停止 ------------------------------------
# ⚠️ 起動した PID（`uv run` / `npm run dev`）だけを kill すると、その下で動く
# uvicorn（reloader とワーカー）や vite が残り、次回の起動でポートが塞がる。
# 子孫プロセスまでたどって止める。
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "${pid}" 2>/dev/null); do
    kill_tree "${child}"
  done
  kill "${pid}" 2>/dev/null || true
}

BACK_PID=""
FRONT_PID=""
CLEANED=""
cleanup() {
  [ -n "${CLEANED}" ] && return 0
  CLEANED=1
  echo ""
  echo "==> 停止処理中..."
  [ -n "${FRONT_PID}" ] && kill_tree "${FRONT_PID}"
  [ -n "${BACK_PID}" ] && kill_tree "${BACK_PID}"
  return 0
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
