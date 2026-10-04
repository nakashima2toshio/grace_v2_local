"""GRACE-Review の E2E: 画面の 3 つの例文を、実 LLM・実 Embedding・実データ（規程）で流す。

ケースは `ReviewForm.tsx` の `EXAMPLES`。期待値は `cases.py`。
とくに「適正LP案 → 指摘 0 件」は過検知の回帰を捕まえる（`backend/docs/tests.md` §4.2）。
"""

import pytest

from backend.app.core.review_agent import run_review_agent_core
from backend.app.core.rulesets import RULESETS
from backend.tests.e2e.cases import (
    REVIEW_EXPECT,
    REVIEW_WATCH,
    review_examples,
    review_rule_ids,
)

pytestmark = pytest.mark.e2e

EXAMPLES = review_examples()
RULESET = "ec_ad"


@pytest.mark.parametrize("title", list(REVIEW_EXPECT))
def test_review_example(title, require_collections, run_options, record, api_errors):
    # 規程（ec_ad_rules）と社内規程（ec_policy）の両方が要る。OK 例の
    # 「返品 14 日」は ec_policy の 14 日と比べて初めて「問題なし」になる
    counts = require_collections(RULESETS[RULESET].collections)
    expect = REVIEW_EXPECT[title]
    errors = []

    result = run_review_agent_core(
        EXAMPLES[title], document_title=title, ruleset=RULESET,
        emit=lambda e: errors.append(e.message) if e.type == "error" else None,
        **run_options,
    )

    findings = result.findings if result else []
    record(
        kind="review", title=title, collections=counts, errors=errors,
        findings=[
            {"rule_id": f.rule_id, "severity": f.severity, "status": f.status,
             "excerpt": f.excerpt, "message": f.message}
            for f in findings
        ],
        summary=getattr(result, "summary", None),
        rules_evaluated=getattr(result, "rules_evaluated", None),
        forced_high=getattr(result, "forced_high", None),
        # 落とさないが記録する期待値（cases.REVIEW_WATCH）。揺れは GRACE_E2E_REPEAT で測る
        missing_expected=[r for r in REVIEW_WATCH.get(title, []) if r not in review_rule_ids(findings)],
        api_errors=api_errors(),
    )
    assert errors == [], f"パイプラインがエラーを出した: {errors}"
    assert result is not None
    # Detect が失敗すると全ルールが「要確認」で残り、「指摘が出る」期待を満たしてしまう
    assert api_errors() == [], "API 呼び出しが失敗した（結果は安全側へ倒れただけで意味を持たない）"
    failed = [f.rule_id for f in findings if "自動判定に失敗" in f.message]
    assert failed == [], f"Detect が失敗したルール: {failed}"

    rule_ids = review_rule_ids(findings)
    if "max_findings" in expect:
        assert len(findings) <= expect["max_findings"], f"過検知: {rule_ids}"
    if "min_findings" in expect:
        assert len(findings) >= expect["min_findings"], "指摘が出なかった"
    if "min_high" in expect:
        assert result.summary.high >= expect["min_high"], f"high が無い: {rule_ids}"
    for rule_id in expect.get("rule_ids", []):
        assert rule_id in rule_ids, f"{rule_id} が出なかった: {rule_ids}"
