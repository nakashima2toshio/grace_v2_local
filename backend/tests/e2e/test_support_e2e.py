"""GRACE-Support の E2E: 業界ごとの例文を、実 LLM・実 Embedding・実データで流す。

ケースは画面の例文（`QueryForm.tsx` の `VERTICAL_EXAMPLES`）。期待値は `cases.py`。
LLM の言い回しには依存させず、**判定・出典・アクション**の筋が通っているかを見る。
回答の中身はレポート JSON（`logs/e2e/`）で人が読む。
"""

import pytest

from backend.app.core.support_agent import run_support_agent_core
from backend.app.core.verticals import PROFILES
from backend.tests.e2e.cases import (
    SUPPORT_EXPECT,
    first_internal_citation,
    support_examples,
)

pytestmark = pytest.mark.e2e

EXAMPLES = support_examples()


def _run(vertical, require_collections, run_options, record, api_errors):
    # 業界プロファイルの検索スコープのうち、少なくとも 1 つに実データが要る
    counts = require_collections(PROFILES[vertical].collections, any_of=True)
    query = EXAMPLES[vertical]
    errors = []
    result = run_support_agent_core(
        query, vertical=vertical,
        emit=lambda e: errors.append(e.message) if e.type == "error" else None,
        **run_options,
    )
    record(
        kind="support", vertical=vertical, query=query, collections=counts,
        errors=errors,
        decision=getattr(result, "decision", None),
        answer=getattr(result, "answer", None),
        citations=getattr(result, "citations", None),
        groundedness=getattr(result, "groundedness", None),
        groundedness_decided=getattr(result, "groundedness_decided", None),
        forced_escalate=getattr(result, "forced_escalate", None),
        no_info_detected=getattr(result, "no_info_detected", None),
        action=getattr(getattr(result, "action", None), "action_type", None),
        action_result=getattr(result, "action_result", None),
        api_errors=api_errors(),
    )
    assert errors == [], f"パイプラインがエラーを出した: {errors}"
    # API が落ちると安全側（エスカレ）に倒れて「それらしく」通ってしまうので先に見る
    assert api_errors() == [], "API 呼び出しが失敗した（結果は安全側へ倒れただけで意味を持たない）"
    assert result is not None
    return result


def test_gov_answers_from_internal_knowledge(require_collections, run_options, record, api_errors):
    """住民票の写しの取り方 → 社内ナレッジ（gov）を出典に回答する。"""
    assert SUPPORT_EXPECT["gov"] == "answer_from_internal"
    result = _run("gov", require_collections, run_options, record, api_errors)

    assert result.decision == "answer", f"エスカレになった（回答: {result.answer!r}）"
    assert result.answer and result.answer.strip()
    assert first_internal_citation(result.citations), f"社内ナレッジの出典が無い: {result.citations}"
    assert result.groundedness_decided > 0, "根拠検証で判定できた主張が 0 件"
    assert not result.no_info_detected


def test_saas_outage_is_escalated_by_keyword(require_collections, run_options, record, api_errors):
    """「落ち」はエスカレーション語 → LLM の判断に関係なく有人へ引き継ぐ。"""
    assert SUPPORT_EXPECT["saas"] == "forced_escalate"
    result = _run("saas", require_collections, run_options, record, api_errors)

    assert result.forced_escalate is True
    assert result.decision == "escalate"
    assert result.action is not None and result.action.action_type == "escalate_to_human"


def test_ec_return_request_runs_a_consistent_action(require_collections, run_options, record, api_errors):
    """「返品したい」→ 回答できればチケット起票、できなければ有人引き継ぎ（本人確認つき）。"""
    assert SUPPORT_EXPECT["ec"] == "action_consistent"
    result = _run("ec", require_collections, run_options, record, api_errors)

    assert result.action is not None, "返品の依頼なのにアクションが選ばれなかった"
    # 社内規程（ec_policy / ec_faq）を検索して引いていること
    assert first_internal_citation(result.citations), f"社内ナレッジの出典が無い: {result.citations}"
    expected = "create_ticket" if result.decision == "answer" else "escalate_to_human"
    assert result.action.action_type == expected
    # ec は本人確認が必須（ドライランではデモ照合で確認済みになる）
    assert result.identity_checked is True
    assert result.action_result
