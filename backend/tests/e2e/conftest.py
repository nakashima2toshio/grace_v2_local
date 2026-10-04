"""E2E（実 LLM・実 Embedding・実データ入り Qdrant）の共通フィクスチャ。

`backend/tests` の他のテストと違い、**本物のローカル LLM（Ollama）と Gemini Embedding を呼ぶ**
（Embedding は課金される・LLM は数分かかる）。そのため `GRACE_E2E=1` を明示したときだけ走り、
それ以外は skip する（CI も skip）。

⚠️ **Mac 専用。** クラウド VM には Ollama が無い（CPU 4 コア・GPU なし）ので走らない。
実データは Mac の Qdrant（grace_v2 と共用）をそのまま使う。

走る条件（足りなければ、足りないものを理由に出して skip）:

| 条件 | 用意の仕方 |
|---|---|
| `GRACE_E2E=1` | 実行時に付ける（`GRACE_E2E=1 uv run --no-sync pytest backend/tests/e2e -m e2e -rs`） |
| Ollama | `ollama serve` と、使うモデルの `ollama pull`（既定は `config.get_default_ollama_model()`） |
| `GOOGLE_API_KEY` | `.env`（Embedding 用） |
| Qdrant に実データ | Mac の Qdrant（grace_v2 と共用）に登録済みであること |

環境変数:
    GRACE_E2E_MODEL     使う LLM（既定: config.get_default_ollama_model()）
    GRACE_E2E_USE_WEB   1 で Web 検索も使う（既定 0。外部サイトに結果が左右されるため）。
                        0 のとき Support は ⑤ に加えて executor の Web 検索も止まる
                        （2026-10-04 以前は ⑤ しか止まらなかった。各テストが確かめる）
    GRACE_E2E_REPORT    結果 JSON の出力先（既定: logs/e2e/e2e_<日時>.json）

結果（回答・出典・判定・指摘・所要時間）は JSON に書き出す。合否だけでなく、
**回答の中身を人が読んで確かめる**ためのもの。

⚠️ **API が失敗してもパイプラインは例外を出さず、安全側の結果を返す**（Support は
エスカレ、Review は全ルールを「要確認」で残す）。そのため期待値によっては、
キーが無効でも合格してしまう（2026-10-03 にダミーキーで実測: 6 件中 4 件が passed）。
これを防ぐため 2 段で見張る:

1. `e2e_ready` が最初に Ollama（モデルが pull 済みか）と Gemini を確かめる（駄目なら全件 ERROR）
2. `api_errors` が実行中の API エラーのログ（401 / 400 / レート制限 / 過負荷 など）を拾い、
   各テストは 1 件でもあれば fail にする
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pytest

from backend.tests.integration.conftest import QDRANT_URL, START_HINT, _reachable

RESTORE_HINT = (
    "Mac の Qdrant に該当コレクションを登録する（grace_v2 と共用。"
    "grace_v2 側の scripts/qdrant_snapshot.py list で状態を見られる）"
)

_records: List[Dict] = []


@pytest.fixture(scope="session")
def e2e_ready():
    """E2E を走らせてよいか（オプトイン・キー・Qdrant）。"""
    if os.getenv("GRACE_E2E") != "1":
        pytest.skip("GRACE_E2E=1 のときだけ走る（実 LLM・実 Embedding を呼ぶので課金される）")
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("API キーが無い: GOOGLE_API_KEY（Embedding 用。.env に設定する）")
    from config import OllamaConfig

    if not _reachable(OllamaConfig.BASE_URL, 11434):
        pytest.skip(f"Ollama に接続できない（{OllamaConfig.BASE_URL}）。起動: ollama serve")
    if not _reachable(QDRANT_URL, 6333):
        pytest.skip(f"Qdrant に接続できない（{QDRANT_URL}）。起動: {START_HINT}")

    _preflight()

    from qdrant_client import QdrantClient

    client = QdrantClient(url=QDRANT_URL, timeout=10)
    yield client
    client.close()


def _preflight() -> None:
    """LLM とキーが本当に使えるかを確かめる（安全側の結果で合格させないため）。"""
    try:
        import httpx

        from config import OllamaConfig, get_default_ollama_model

        model = os.getenv("GRACE_E2E_MODEL") or get_default_ollama_model()
        listed = httpx.get(f"{OllamaConfig.BASE_URL.rstrip('/')}/models", timeout=10).json()
        names = {m.get("id") for m in listed.get("data", [])}
        assert model in names, f"モデル {model} が pull されていない（ollama pull {model}）。あるもの: {sorted(names)}"
    except Exception as e:
        pytest.fail(f"Ollama を使えない: {type(e).__name__}: {str(e)[:300]}", pytrace=False)
    # 次元は GeminiConfig が持つ（grace_v2 と違い ModelConfig には無い）。
    # 呼び出しの失敗と分けて判定し、次元違いを「キーが無効」と表示しない
    from config import GeminiConfig
    from qdrant_client_wrapper import embed_query

    try:
        vector = embed_query("疎通確認")
    except Exception as e:
        pytest.fail(f"Gemini Embedding を呼べない（キーが無効か、ネットワークで拒否）: "
                    f"{type(e).__name__}: {str(e)[:200]}", pytrace=False)
    if len(vector) != GeminiConfig.EMBEDDING_DIMS:
        pytest.fail(f"Gemini Embedding の次元が {len(vector)}（期待 {GeminiConfig.EMBEDDING_DIMS}）。"
                    "Qdrant のコレクションと合わない", pytrace=False)


# API 失敗を表すログ。パイプラインはこれを握って安全側へ倒すので、ログで拾う
_API_ERROR_MARKERS = (
    "Error code: 4", "Error code: 5", "INVALID_ARGUMENT", "PERMISSION_DENIED",
    "RESOURCE_EXHAUSTED", "authentication_error", "rate_limit", "overloaded",
    "RAGツールエラー", "Embedding生成エラー",
    # Ollama（OpenAI 互換クライアント）の接続失敗
    "Connection error", "APIConnectionError", "SchemaEchoError",
)


@pytest.fixture
def api_errors(caplog):
    """`api_errors()` — このテスト中に出た API エラーのログ（WARNING 以上）。"""
    caplog.set_level(logging.WARNING)

    def _collect() -> List[str]:
        return [
            f"{r.name}: {r.getMessage()[:200]}"
            for r in caplog.records
            if r.levelno >= logging.WARNING and any(m in r.getMessage() for m in _API_ERROR_MARKERS)
        ]

    return _collect


@pytest.fixture(scope="session")
def require_collections(e2e_ready):
    """`require_collections(names, any_of=False)` — 実データが無ければ skip。"""

    def _require(names: List[str], any_of: bool = False) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for name in names:
            if e2e_ready.collection_exists(name):
                counts[name] = e2e_ready.count(name, exact=True).count
        present = [n for n in names if counts.get(n)]
        ok = bool(present) if any_of else len(present) == len(names)
        if not ok:
            lacking = [n for n in names if not counts.get(n)]
            need = "どれか 1 つ" if any_of else "すべて"
            pytest.skip(f"実データが無い（{need}必要）: {', '.join(lacking)}。{RESTORE_HINT}")
        return counts

    return _require


@pytest.fixture(scope="session")
def run_options():
    """コアへ渡す共通の実行条件。"""
    return {
        "model": os.getenv("GRACE_E2E_MODEL") or None,
        "use_web": os.getenv("GRACE_E2E_USE_WEB") == "1",
        "dry_run": True,     # アクションは必ずドライラン（外部へ副作用を出さない）
        "do_action": True,
    }


@pytest.fixture(scope="session")
def sparse_available(e2e_ready) -> bool:
    """hybrid 検索の sparse Embedding が使えるか（使えないと dense だけで検索する）。

    VM の既定のネットワーク設定では huggingface.co へ届かず、モデルを取得できない。
    結果が Mac と変わりうるので、レポートに残す。
    """
    try:
        from qdrant_client_wrapper import embed_sparse_query_unified

        embed_sparse_query_unified("テスト")
        return True
    except Exception:
        return False


@pytest.fixture
def record(request, run_options, sparse_available):
    """`record(**data)` — このケースの結果をレポートへ積む。"""
    started = time.monotonic()

    def _record(**data):
        _records.append({
            "test": request.node.nodeid,
            "elapsed_sec": round(time.monotonic() - started, 1),
            "model": run_options["model"] or "(config llm.model)",
            "use_web": run_options["use_web"],
            "sparse": sparse_available,
            **data,
        })

    return _record


def pytest_sessionfinish(session, exitstatus):
    if not _records:
        return
    default = Path("logs/e2e") / f"e2e_{datetime.now():%Y%m%d_%H%M%S}.json"
    path = Path(os.getenv("GRACE_E2E_REPORT") or default)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[e2e] 結果を書き出しました: {path}（{len(_records)} 件）")
