# backend/tests/test_groundedness_sources.py
"""P-01（groundedness へ出典本文を渡す）の回帰テスト。

背景（`docs/performance_levers.md` §2 P-01）:
③ Confidence は以前、出典**識別子**（ファイル名）だけを
`GroundednessVerifier.verify()` へ渡していた。「情報源: gov_faq.csv」では
どの主張も裏付けられず全て neutral（支持率の分母 0）になり、不当に
escalate へ倒れる原因になっていた。

検証すること:
- 出典本文（`StepResult.source_texts`）があれば、それが検証器へ渡る
- 本文が無い経路（legacy agent 等）では従来の出典ラベルへフォールバックする
- `_collect_source_texts` の重複排除・欠損耐性
- executor 側の `_extract_source_texts` が payload から本文を組み立てる
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.app.core.gates import _collect_source_texts
from backend.app.core.support_agent import run_support_agent_core
from backend.tests.conftest import PipelineStub, install_pipeline_stub

# ---------------------------------------------------------------------------
# _collect_source_texts（純関数）
# ---------------------------------------------------------------------------

def test_collect_source_texts_dedupes_and_preserves_order():
    """重複を除き、出現順を保って本文を集約する。"""
    step_results = [
        SimpleNamespace(source_texts=["Q: A?\nA: B", "本文2"]),
        SimpleNamespace(source_texts=["本文2", "本文3"]),
    ]
    assert _collect_source_texts(step_results) == ["Q: A?\nA: B", "本文2", "本文3"]


def test_collect_source_texts_tolerates_missing_attribute():
    """source_texts を持たないステップ（旧経路）でも例外にならず空を返す。"""
    step_results = [SimpleNamespace(sources=["faq.md"])]  # source_texts なし
    assert _collect_source_texts(step_results) == []


def test_collect_source_texts_skips_empty_and_none():
    """空文字・None は除外する。"""
    step_results = [
        SimpleNamespace(source_texts=["", "本文"]),
        SimpleNamespace(source_texts=None),
    ]
    assert _collect_source_texts(step_results) == ["本文"]


def test_collect_source_texts_handles_empty_input():
    """step_results が空/None でも空リストを返す。"""
    assert _collect_source_texts([]) == []
    assert _collect_source_texts(None) == []


# ---------------------------------------------------------------------------
# パイプライン結線（③ Confidence が検証器へ何を渡すか）
# ---------------------------------------------------------------------------

def test_verifier_receives_source_texts_not_filenames(monkeypatch):
    """本文がある場合、検証器へは**本文**が渡る（ファイル名ではない）。"""
    stub = PipelineStub(
        sources=["gov_faq.csv"],
        source_texts=["Q: 住民票の取り方\nA: 窓口またはコンビニ交付で請求できます。"],
    )
    install_pipeline_stub(monkeypatch, stub)

    run_support_agent_core("住民票の写しの取り方は？", vertical="gov")

    assert stub.verify_calls, "verify が呼ばれていない"
    passed = stub.verify_calls[0]
    assert passed == ["Q: 住民票の取り方\nA: 窓口またはコンビニ交付で請求できます。"]
    # 出典識別子がそのまま渡っていないこと（P-01 の再発防止）
    assert "gov_faq.csv" not in passed


def test_verifier_falls_back_to_citation_labels_without_source_texts(monkeypatch):
    """本文が無い経路では従来どおり出典ラベル（識別子）で検証する。"""
    stub = PipelineStub(sources=["gov_faq.csv"], source_texts=[])
    install_pipeline_stub(monkeypatch, stub)

    run_support_agent_core("住民票の写しの取り方は？", vertical="gov")

    assert stub.verify_calls, "verify が呼ばれていない"
    # ラベル（"[社内] "）は除去された中身が渡る
    assert stub.verify_calls[0] == ["gov_faq.csv"]


def test_citations_still_show_identifiers_not_body_text(monkeypatch):
    """出典表示は識別子のまま（本文が混入しない）。"""
    stub = PipelineStub(
        sources=["gov_faq.csv"],
        source_texts=["Q: 住民票の取り方\nA: 窓口で請求できます。"],
    )
    install_pipeline_stub(monkeypatch, stub)

    result = run_support_agent_core("住民票の写しの取り方は？", vertical="gov")

    assert result is not None
    assert result.citations == ["[社内] gov_faq.csv"]


# ---------------------------------------------------------------------------
# executor 側の本文抽出
# ---------------------------------------------------------------------------

def _extract(output):
    """Executor._extract_source_texts を最小の self で呼ぶ。"""
    from grace.executor import Executor

    return Executor._extract_source_texts(
        SimpleNamespace(), SimpleNamespace(output=output)
    )


def test_extract_source_texts_builds_qa_format():
    """question と answer が揃う FAQ は "Q: …\\nA: …" に整形する。"""
    output = [{"payload": {"question": "住民票の取り方", "answer": "窓口で請求できます。",
                           "source": "gov_faq.csv"}}]
    assert _extract(output) == ["Q: 住民票の取り方\nA: 窓口で請求できます。"]


def test_extract_source_texts_prefers_answer_then_content():
    """question が無い場合は answer、無ければ content を使う。"""
    assert _extract([{"payload": {"answer": "本文A"}}]) == ["本文A"]
    assert _extract([{"payload": {"content": "本文C"}}]) == ["本文C"]


def test_extract_source_texts_dedupes_and_skips_empty():
    """重複は除去し、本文が無い payload は無視する。"""
    output = [
        {"payload": {"answer": "同じ本文"}},
        {"payload": {"answer": "同じ本文"}},
        {"payload": {"source": "only_id.csv"}},  # 本文なし → 無視
    ]
    assert _extract(output) == ["同じ本文"]


def test_extract_source_texts_returns_empty_for_non_list_output():
    """出力がリストでない（reasoning の文字列等）場合は空を返す。"""
    assert _extract("これは文字列の回答です") == []
    assert _extract(None) == []


# ---------------------------------------------------------------------------
# P-01b: executor 内部（overall_confidence 用）の出典本文集約
#
# `_calculate_overall_confidence` は自己評価（evaluate_final）と groundedness
# ブレンドの双方で「回答が情報源に裏付けられているか」を判定するため、識別子で
# はなく本文を渡す必要がある。識別子のままだと
# 「Groundedness neutral (0 decided of N)」となりフォールバック値に落ちる。
# ---------------------------------------------------------------------------

def _completed_source_texts(step_results):
    """ExecutionState.get_completed_source_texts を最小の self で呼ぶ。"""
    from grace.executor import ExecutionState

    return ExecutionState.get_completed_source_texts(
        SimpleNamespace(step_results=step_results)
    )


def test_completed_source_texts_collects_and_dedupes():
    """成功ステップの本文を出現順に集約し、重複を除く。"""
    step_results = {
        1: SimpleNamespace(status="success", source_texts=["本文A", "本文B"]),
        2: SimpleNamespace(status="success", source_texts=["本文B", "本文C"]),
    }
    assert _completed_source_texts(step_results) == ["本文A", "本文B", "本文C"]


def test_completed_source_texts_skips_failed_steps():
    """失敗ステップの本文は集約しない（get_completed_sources と同じ扱い）。"""
    step_results = {
        1: SimpleNamespace(status="failed", source_texts=["失敗の本文"]),
        2: SimpleNamespace(status="success", source_texts=["成功の本文"]),
    }
    assert _completed_source_texts(step_results) == ["成功の本文"]


def test_completed_source_texts_tolerates_missing_attribute():
    """source_texts を持たない経路（legacy agent 等）では空を返す。"""
    step_results = {1: SimpleNamespace(status="success", sources=["faq.md"])}
    assert _completed_source_texts(step_results) == []


def test_completed_source_texts_skips_empty_values():
    """空文字・None は除外する。"""
    step_results = {
        1: SimpleNamespace(status="success", source_texts=["", None, "本文"]),
    }
    assert _completed_source_texts(step_results) == ["本文"]


def test_completed_source_texts_empty_when_no_steps():
    """ステップが無ければ空リスト（呼び出し側が識別子へフォールバックできる）。"""
    assert _completed_source_texts({}) == []
