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

# Flower起動
start_flower() {
    echo "Flowerを起動中 (ポート: $FLOWER_PORT)..."

    nohup "${CELERY_CMD[@]}" -A celery_config flower \
        --port=$FLOWER_PORT \
        > "$LOG_DIR/flower.log" 2>&1 &

    sleep 2

    if pgrep -f "$FLOWER_PROCESS_PATTERN" > /dev/null; then
        echo "✅ Flower起動: http://localhost:$FLOWER_PORT"
    else
        echo "❌ Flower起動失敗"
        echo "ログ確認: tail -50 $LOG_DIR/flower.log"
    fi
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

    sleep 3

    if pgrep -f "$WORKER_PROCESS_PATTERN" > /dev/null; then
        echo "✅ Celeryワーカー起動完了 (concurrency=$CONCURRENCY)"

        # Flowerも起動する場合
        if [ "$START_FLOWER" = true ]; then
            start_flower
        fi
    else
        echo "❌ ワーカー起動失敗"
        echo "ログ確認: tail -50 $LOG_DIR/celery_qa_worker.log"
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

# 依存の入れ直し方を案内する。
#
# ⚠️ **素の `pip install` を勧めてはいけない。** 本リポジトリの .venv は
#    `uv sync --extra dev`（run_dev.sh）が pyproject.toml から作る。ここで
#    `pip install "celery==5.5.3"` を打たせると pyproject の `celery>=5.6.3`
#    と衝突するダウングレードになり、しかも次の `uv sync` で巻き戻る。
suggest_repair() {
    if [ -n "${VIRTUAL_ENV:-}" ] || [ "$PYTHON_BIN" = "$PROJECT_ROOT/.venv/bin/python3" ]; then
        echo "  修復: uv sync --extra dev        # pyproject.toml から .venv へ入れ直す"
        echo "        （それでも入らないなら: uv pip install \"flower>=2.0.1\"）"
    else
        echo "  修復: $PYTHON_BIN -m pip install \"flower>=2.0.1\""
        echo "        （このリポジトリの .venv を使うなら: source .venv/bin/activate && uv sync --extra dev）"
    fi
}

# Flower を現在の Python 環境から確実に読めるか検証する。
check_celery_runtime() {
    # ⚠️ **ここで先に古いログを消す。** logs/flower.log は start_flower が
    #    走ったときだけ上書きされる。この検査で exit すると前回の実行の
    #    ログがそのまま残り、`cat logs/flower.log` が**今回とは無関係の
    #    エラー**を見せて混乱の元になる（実際にそうなった）。
    : > "$LOG_DIR/flower.log"

    if ! "$PYTHON_BIN" -c 'import celery, flower' > /dev/null 2>&1; then
        echo "❌ Flower が Python 環境にありません: $PYTHON_BIN"
        suggest_repair
        exit 1
    fi

    if ! "${CELERY_CMD[@]}" --help 2>&1 | grep -q '^  flower'; then
        echo "❌ Flower コマンドを Celery が登録できません: $PYTHON_BIN"
        echo "  （import はできるのに celery のサブコマンドとして出てこない状態）"
        suggest_repair
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
