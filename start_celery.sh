#!/bin/bash
# ============================================================================
# start_celery.sh - Celeryワーカー + Flower 起動スクリプト（改修版）
# ============================================================================
#
# 【用語説明】
#   - ワーカープロセス: Celeryワーカーの台数（本スクリプトでは1台固定）
#   - concurrency: 1ワーカーあたりの並列タスク数（-c オプションで指定）
#   - 合計処理能力: ワーカー数 × concurrency
#
# 【使用方法】
#   ./start_celery.sh start                    # デフォルト（concurrency=8）
#   ./start_celery.sh start -c 4               # concurrency=4で起動
#   ./start_celery.sh start -c 8 --flower      # concurrency=8 + Flower
#   ./start_celery.sh stop                     # 停止
#   ./start_celery.sh restart -c 8 --flower    # 再起動
#   ./start_celery.sh status                   # 状態確認
#
# 【推奨設定（M2 MacBook Air, 24GB RAM, 8 vCPU）】
#   ./start_celery.sh restart -c 8 --flower
#
# 【make_qa_register_qdrant.py との連携例】
#   # 1. Celeryワーカー起動
#   ./start_celery.sh restart -c 8 --flower
#
#   # 2. Q/A生成 + Qdrant登録
#   python qa_qdrant/make_qa_register_qdrant.py \
#     --input-file output_chunked/cc_news_5per_chunks.csv \
#     --collection cc_news_5per \
#     --use-celery \
#     --recreate
#
# ============================================================================

set -e

# プロジェクトルート
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
cd "$PROJECT_ROOT"

# ログディレクトリ
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

# 環境変数
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/helper"

# Celery の CLI とパッケージを同じ Python 環境から起動する。
# `celery` を PATH から直接呼ぶと、`python3 -m pip install flower` を
# 実行した環境とは別の Celery CLI が選ばれ、flower サブコマンドだけが
# 見つからないことがある。
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
CELERY_CMD=("$PYTHON_BIN" -m celery)
CELERY_PROCESS_PATTERN="python[0-9.]*.*-m celery -A celery_config"
WORKER_PROCESS_PATTERN="$CELERY_PROCESS_PATTERN worker"
FLOWER_PROCESS_PATTERN="$CELERY_PROCESS_PATTERN flower"

# キュー設定
QUEUES="celery,high_priority,normal_priority,low_priority"

# デフォルト設定
CONCURRENCY=8         # 並列タスク数（1ワーカーあたり）
LOGLEVEL="INFO"
FLOWER_PORT=5555
START_FLOWER=false

# ヘルプ表示
show_help() {
    echo "============================================================================"
    echo "start_celery.sh - Celeryワーカー + Flower 起動スクリプト"
    echo "============================================================================"
    echo ""
    echo "使用方法: $0 {start|stop|restart|status} [-c concurrency] [--flower] [--flower-port PORT]"
    echo ""
    echo "コマンド:"
    echo "  start   - ワーカーを起動"
    echo "  stop    - ワーカーを停止"
    echo "  restart - ワーカーを再起動"
    echo "  status  - ワーカーの状態を表示"
    echo ""
    echo "オプション:"
    echo "  -c, --concurrency  並列タスク数 (デフォルト: 8)"
    echo "  -w, --workers      -c の別名（後方互換性）"
    echo "  --flower           Flowerも起動"
    echo "  --flower-port      Flowerポート (デフォルト: 5555)"
    echo ""
    echo "例:"
    echo "  $0 start -c 8 --flower      # concurrency=8 + Flower"
    echo "  $0 start -c 4               # concurrency=4（軽量モード）"
    echo "  $0 restart -c 8 --flower    # 再起動"
    echo "  $0 stop                     # 停止"
    echo "  $0 status                   # 状態確認"
    echo ""
    echo "推奨設定（M2 MacBook Air）:"
    echo "  $0 restart -c 8 --flower"
    echo "============================================================================"
}

# 全プロセス強制終了
kill_all_celery() {
    echo "Celery関連プロセスを強制終了中..."

    # "celery -A" にマッチするプロセスを終了（start_celery.sh自体は除外される）
    pkill -9 -f "celery -A" 2>/dev/null || true
    pkill -9 -f "celery worker" 2>/dev/null || true
    pkill -9 -f "celery flower" 2>/dev/null || true
    pkill -9 -f "$CELERY_PROCESS_PATTERN" 2>/dev/null || true

    sleep 2

    # 確認
    remaining=$(pgrep -f "celery -A|$CELERY_PROCESS_PATTERN" 2>/dev/null | wc -l || echo 0)
    if [ "$remaining" -eq 0 ]; then
        echo "✅ 全プロセス停止完了"
    else
        echo "⚠️ 残存プロセス:"
        pgrep -af "celery -A|$CELERY_PROCESS_PATTERN" 2>/dev/null || true
    fi
}

# ワーカー停止
stop_workers() {
    kill_all_celery
}

# 失敗したときは「ログを見てください」で終わらせず、**その場で原因を出す**。
# 別ターミナルで tail する往復を挟むと、原因に届くまでが 1 手増える。
report_failure() {
    local label="$1" logfile="$2"

    echo "❌ ${label}起動失敗"
    echo "--- $logfile （末尾 30 行）------------------------------------------"
    tail -30 "$logfile" 2>/dev/null || echo "（ログがありません）"
    echo "------------------------------------------------------------------"

    # よくある原因に当たりを付けて、次の一手まで出す
    if grep -qiE "Address already in use|Errno 4[89]" "$logfile" 2>/dev/null; then
        echo "→ ポート $FLOWER_PORT が既に使われています。占有しているプロセス:"
        lsof -nP -iTCP:"$FLOWER_PORT" -sTCP:LISTEN 2>/dev/null || echo "  （lsof では特定できず）"
        echo "  対処: --flower-port 5556 のように別ポートを指定するか、上のプロセスを止める"
    elif grep -qiE "No module named|ModuleNotFoundError|ImportError" "$logfile" 2>/dev/null; then
        echo "→ 依存の不足です。"
        echo "  修復: $PYTHON_BIN -m pip install \"celery==5.5.3\" \"flower==2.0.1\""
    elif grep -qiE "Connection refused|OperationalError" "$logfile" 2>/dev/null; then
        echo "→ ブローカー（Redis）へ接続できていません。brew services start redis を確認"
    fi
}

# ポートが空いているか。python で実際に bind して確かめるので、
# lsof / nc / netstat の有無や OS 差に依存しない。
port_is_free() {
    "$PYTHON_BIN" - "$1" <<'PYCHECK' > /dev/null 2>&1
import socket
import sys

sock = socket.socket()
try:
    sock.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PYCHECK
}

# Flower起動
start_flower() {
    echo "Flowerを起動中 (ポート: $FLOWER_PORT)..."

    # ⚠️ **先にポートを見る。** Flower は bind に失敗する場合でも
    #    `Visit me at http://0.0.0.0:$FLOWER_PORT` を**出してから**落ちるため、
    #    ログの見た目だけでは起動したように見えてしまう。
    if ! port_is_free "$FLOWER_PORT"; then
        echo "❌ ポート $FLOWER_PORT は既に使用中です。Flower を起動できません。"
        lsof -nP -iTCP:"$FLOWER_PORT" -sTCP:LISTEN 2>/dev/null || true
        echo "  対処: --flower-port 5556 のように別ポートを指定するか、上のプロセスを止める"
        return 1
    fi

    nohup "${CELERY_CMD[@]}" -A celery_config flower \
        --port=$FLOWER_PORT \
        > "$LOG_DIR/flower.log" 2>&1 &
    local flower_pid=$!

    # ⚠️ **起動確認は PID で行う（pgrep のパターン照合ではない）。**
    #    パターンは PYTHON_BIN の実体（pyenv shim・venv・python3.13 など）で
    #    合わなくなることがあり、生きているのに「失敗」と表示する事故になる。
    #    ここで見たいのは「いま起動したプロセス」なので、$! が唯一正確な相手。
    #    あわせて、固定の sleep ではなく**待ち受け開始まで待つ**。
    local waited=0
    while [ "$waited" -lt 15 ]; do
        if ! kill -0 "$flower_pid" 2>/dev/null; then
            report_failure "Flower" "$LOG_DIR/flower.log"
            return 1
        fi
        if ! port_is_free "$FLOWER_PORT"; then
            echo "✅ Flower起動: http://localhost:$FLOWER_PORT （PID: $flower_pid）"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    echo "⚠️ Flower のプロセスは生きていますが、${waited} 秒待っても待ち受けを始めません (PID: $flower_pid)"
    echo "ログ確認: tail -50 $LOG_DIR/flower.log"
    return 1
}

# ワーカー起動
start_workers() {
    # まず残存プロセスを強制終了
    kill_all_celery

    echo ""
    echo "============================================"
    echo "Celeryワーカーを起動中..."
    echo "============================================"
    echo "  並列タスク数 (concurrency): $CONCURRENCY"
    echo "  監視キュー: $QUEUES"
    echo "  ログファイル: $LOG_DIR/celery_qa_worker.log"
    echo "============================================"

    nohup "${CELERY_CMD[@]}" -A celery_config worker \
        --loglevel=$LOGLEVEL \
        --concurrency=$CONCURRENCY \
        -Q $QUEUES \
        -n qa_worker@%h \
        > "$LOG_DIR/celery_qa_worker.log" 2>&1 &
    local worker_pid=$!

    sleep 3

    # Flower と同じく PID で確認する（pgrep のパターン照合に頼らない）
    if kill -0 "$worker_pid" 2>/dev/null; then
        echo "✅ Celeryワーカー起動完了 (concurrency=$CONCURRENCY, PID: $worker_pid)"

        # Flowerも起動する場合。**Flower が失敗してもワーカーは残す。**
        # Flower は監視用の付属品で、Q/A 生成そのものには要らない
        if [ "$START_FLOWER" = true ]; then
            start_flower || echo "⚠️ Flower は起動しませんでしたが、ワーカーは動いています。"
        fi
    else
        report_failure "ワーカー" "$LOG_DIR/celery_qa_worker.log"
        exit 1
    fi
}

# ステータス確認
show_status() {
    echo "============================================"
    echo "Celery ステータス"
    echo "============================================"

    if pgrep -f "$WORKER_PROCESS_PATTERN" > /dev/null; then
        echo "✅ ワーカー: 起動中"

        # Pythonでconcurrencyを取得
        python3 -c "
from celery_config import app
inspect = app.control.inspect()
stats = inspect.stats()
if stats:
    for worker, info in stats.items():
        pool = info.get('pool', {})
        concurrency = pool.get('max-concurrency', 'N/A')
        print(f'   ワーカー名: {worker}')
        print(f'   concurrency: {concurrency}')
else:
    print('   統計情報を取得できません')
" 2>/dev/null || echo "   (詳細情報取得失敗)"

    else
        echo "❌ ワーカー: 停止"
    fi

    echo ""
    if pgrep -f "$FLOWER_PROCESS_PATTERN" > /dev/null; then
        echo "✅ Flower: http://localhost:$FLOWER_PORT"
    else
        echo "❌ Flower: 停止"
    fi

    echo "============================================"
}

# Redis確認
check_redis() {
    if redis-cli ping > /dev/null 2>&1; then
        echo "✅ Redis OK"
    else
        echo "❌ Redis停止中"
        echo "起動方法: brew services start redis (macOS)"
        exit 1
    fi
}

# Flower を現在選ばれている Python 環境から確実に読めるか検証する。
# 失敗時は Celery ワーカーを起動せず、原因と修復コマンドを表示する。
check_celery_runtime() {
    if ! "$PYTHON_BIN" -c 'import celery, flower' > /dev/null 2>&1; then
        echo "❌ Flower が Python 環境にありません: $PYTHON_BIN"
        echo "修復: $PYTHON_BIN -m pip install \"celery==5.5.3\" \"flower==2.0.1\""
        exit 1
    fi

    if ! "${CELERY_CMD[@]}" --help 2>&1 | grep -q '^  flower'; then
        echo "❌ Flower コマンドを Celery が登録できません: $PYTHON_BIN"
        echo "修復: $PYTHON_BIN -m pip install --force-reinstall \"celery==5.5.3\" \"flower==2.0.1\""
        exit 1
    fi
}

# メイン処理
COMMAND=${1:-help}
shift || true

# オプション解析
while [[ $# -gt 0 ]]; do
    case $1 in
        -c|--concurrency)
            CONCURRENCY="$2"
            shift 2
            ;;
        -w|--workers)
            # 後方互換性: -w も -c として扱う
            CONCURRENCY="$2"
            shift 2
            ;;
        --flower)
            START_FLOWER=true
            shift
            ;;
        --flower-port)
            FLOWER_PORT="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

case $COMMAND in
    start)
        check_redis
        check_celery_runtime
        start_workers
        ;;
    stop)
        stop_workers
        ;;
    restart)
        stop_workers
        check_redis
        check_celery_runtime
        start_workers
        ;;
    status)
        show_status
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        show_help
        ;;
esac
