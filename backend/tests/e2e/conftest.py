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
    GRACE_E2E_REPEAT    各ケースを N 回流す（既定 1）。LLM の揺れを測るためのもの。
                        JSON の summary にケースごとの合格率・指摘の出現率・平均所要時間が出る
                        （課金は N 倍。まず 3 程度で）

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
from collections import Counter
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
_outcomes: Dict[str, Dict] = {}   # nodeid → {"case", "outcome", "failure"}


def _repeat() -> int:
    try:
        return max(1, int(os.getenv("GRACE_E2E_REPEAT", "1")))
    except ValueError:
        return 1


def pytest_generate_tests(metafunc):
    """GRACE_E2E_REPEAT=N のとき、`record` を使う（＝E2E の）テストを N 回に増やす。"""
    if "e2e_rep" in metafunc.fixturenames and _repeat() > 1:
        metafunc.parametrize("e2e_rep", range(1, _repeat() + 1), ids=lambda i: f"run{i}")


@pytest.fixture
def e2e_rep() -> int:
    """何回目の実行か（GRACE_E2E_REPEAT が 1 のときは常に 1。上の parametrize が上書きする）。"""
    return 1


def _case_key(item) -> str:
    """繰り返しを除いたケース名（例: `test_review_example[化粧品LP案]`）。"""
    callspec = getattr(item, "callspec", None)
    params = [str(v) for k, v in (callspec.params.items() if callspec else []) if k != "e2e_rep"]
    name = getattr(item, "originalname", None) or item.name
    return f"{name}[{'-'.join(params)}]" if params else name


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """合否をレポートへ残す（`record` はアサーションより前に呼ばれるので、合否を知らない）。"""
    report = (yield).get_result()
    if item.get_closest_marker("e2e") is None:
        return
    entry = _outcomes.setdefault(item.nodeid, {"case": _case_key(item), "outcome": "passed"})
    if report.when == "call" or not report.passed:
        if entry["outcome"] == "passed":       # 最初の失敗（setup / call / teardown）を残す
            entry["outcome"] = report.outcome
            if report.failed:
                # 例外の 1 行目（「AssertionError: 回答に社内ナレッジの事実が無い: ...」）を残す
                crash = getattr(report.longrepr, "reprcrash", None)
                text = crash.message if crash else str(report.longreprtext)
                entry["failure"] = (text.strip().splitlines() or [""])[0][:300]


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


def _resolved_models(run_options) -> Dict[str, str]:
    """このセッションで実際に使う LLM（レポートに残す。モデルを替えた結果を比べるため）。

    GRACE_E2E_MODEL で上書きすると、コアは model と light_model の両方を揃える
    （`run_support_agent_core` の上書き処理）ので、ここでも両方に反映する。
    """
    from grace.config import get_config

    llm = get_config().llm
    override = run_options["model"]
    return {"model": override or llm.model, "light_model": override or llm.light_model}


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
def record(request, run_options, sparse_available, e2e_rep):
    """`record(**data)` — このケースの結果をレポートへ積む。"""
    started = time.monotonic()
    models = _resolved_models(run_options)

    def _record(**data):
        _records.append({
            "test": request.node.nodeid,
            "case": _case_key(request.node),
            "run": e2e_rep,
            "elapsed_sec": round(time.monotonic() - started, 1),
            **models,
            "use_web": run_options["use_web"],
            "sparse": sparse_available,
            **data,
        })

    return _record


def summarize(records: List[Dict], outcomes: Dict[str, Dict]) -> Dict[str, Dict]:
    """ケースごとの合格率・指摘の出現率・平均所要時間（GRACE_E2E_REPEAT の揺れを見る）。

    回数は合否（`outcomes`）から数える。パイプラインが例外を出して `record` まで
    届かなかった回も「失敗 1 回」として数えるため。
    """
    by_test = {r["test"]: r for r in records}
    summary: Dict[str, Dict] = {}
    for nodeid, entry in outcomes.items():
        if entry["outcome"] == "skipped":
            continue
        case = summary.setdefault(entry["case"], {
            "runs": 0, "passed": 0, "elapsed": [], "rule_ids": Counter(), "missing": Counter(),
            "failures": [],
        })
        case["runs"] += 1
        case["passed"] += entry["outcome"] == "passed"
        if entry.get("failure"):
            case["failures"].append(entry["failure"])
        rec = by_test.get(nodeid)
        if rec is None:
            continue
        case["elapsed"].append(rec["elapsed_sec"])
        case["rule_ids"].update({f["rule_id"] for f in rec.get("findings") or []})
        case["missing"].update(rec.get("missing_facts") or rec.get("missing_expected") or [])
    return {
        name: {
            "runs": c["runs"],
            "passed": c["passed"],
            "pass_rate": round(c["passed"] / c["runs"], 2),
            "avg_elapsed_sec": round(sum(c["elapsed"]) / len(c["elapsed"]), 1) if c["elapsed"] else None,
            **({"rule_id_rate": {k: round(v / c["runs"], 2) for k, v in sorted(c["rule_ids"].items())}}
               if c["rule_ids"] else {}),
            **({"missing_rate": {k: round(v / c["runs"], 2) for k, v in sorted(c["missing"].items())}}
               if c["missing"] else {}),
            **({"failures": c["failures"]} if c["failures"] else {}),
        }
        for name, c in summary.items()
    }


def pytest_sessionfinish(session, exitstatus):
    if not _records:
        return
    default = Path("logs/e2e") / f"e2e_{datetime.now():%Y%m%d_%H%M%S}.json"
    path = Path(os.getenv("GRACE_E2E_REPORT") or default)
    path.parent.mkdir(parents=True, exist_ok=True)
    for rec in _records:
        rec["outcome"] = _outcomes.get(rec["test"], {}).get("outcome")
    summary = summarize(_records, _outcomes)
    report = {"repeat": _repeat(), "summary": summary, "records": _records}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[e2e] 結果を書き出しました: {path}（{len(_records)} 件）")
    if _repeat() > 1:
        for name, c in summary.items():
            print(f"[e2e]   {name}: {c['passed']}/{c['runs']} 合格・平均 {c['avg_elapsed_sec']} 秒")
