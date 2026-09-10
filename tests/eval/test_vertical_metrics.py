# tests/eval/test_vertical_metrics.py
"""eval/vertical のメトリクス算出とテストケース JSONL の妥当性テスト。

LLM・Qdrant 不要（合成レコードとファイル検証のみ）。
"""
import json
from pathlib import Path

from eval.vertical.metrics import CATEGORIES, CaseResult, compute_metrics, format_table
from eval.vertical.run import CASES_DIR, load_cases

CONFIRM_TH = 0.4


def rec(category, expected_decision, decision, *, expected_action=None, action=None,
        citations=0, groundedness=0.0, decided=1, forced=False, identity=False,
        expect_identity=False, error=None):
    case = {
        "category": category,
        "expected_decision": expected_decision,
        "expected_action": expected_action,
    }
    if expect_identity:
        case["expect_identity_check"] = True
    return CaseResult(
        case=case, decision=decision, action_type=action,
        citation_count=citations, groundedness=groundedness,
        groundedness_decided=decided,
        forced_escalate=forced, identity_checked=identity,
        latency_ms=100.0, error=error,
    )


class TestComputeMetrics:
    def test_all_correct_yields_perfect_scores(self):
        results = [
            rec("in-scope", "answer", "answer", citations=2, groundedness=0.9),
            rec("keyword-trap", "answer", "answer", citations=1, groundedness=0.8),
            rec("out-of-scope", "escalate", "escalate",
                expected_action="escalate_to_human", action="escalate_to_human"),
            rec("action", "answer", "answer", expected_action="create_ticket",
                action="create_ticket", citations=1, groundedness=0.7,
                identity=True, expect_identity=True),
        ]
        m = compute_metrics(results, confirm_th=CONFIRM_TH)
        assert m["samples"] == 4 and m["errors"] == 0
        assert m["decision_accuracy"] == 1.0
        assert m["false_escalate_rate"] == 0.0
        assert m["forced_escalate_misfire_rate"] == 0.0
        assert m["escalate_recall"] == 1.0
        assert m["citation_rate"] == 1.0
        assert m["ungrounded_answer_rate"] == 0.0
        assert m["action_accuracy"] == 1.0
        assert m["identity_check_rate"] == 1.0

    def test_keyword_trap_misfire_is_measured(self):
        # trap 2 件中 1 件が強制エスカレで誤検知 → 誤エスカレ率 0.5・誤検知率 0.5
        results = [
            rec("keyword-trap", "answer", "escalate",
                expected_action=None, action="escalate_to_human", forced=True),
            rec("keyword-trap", "answer", "answer", citations=1, groundedness=0.9),
        ]
        m = compute_metrics(results, confirm_th=CONFIRM_TH)
        assert m["false_escalate_rate"] == 0.5
        assert m["forced_escalate_misfire_rate"] == 0.5
        assert m["decision_accuracy"] == 0.5
        # escalate すべきカテゴリのケースが無い → 分母 0 は None（0.0 と区別）
        assert m["escalate_recall"] is None

    def test_ungrounded_answer_and_missing_citation(self):
        results = [
            rec("in-scope", "answer", "answer", citations=0, groundedness=0.2),
            rec("in-scope", "answer", "answer", citations=2, groundedness=0.9),
        ]
        m = compute_metrics(results, confirm_th=CONFIRM_TH)
        assert m["citation_rate"] == 0.5
        assert m["ungrounded_answer_rate"] == 0.5

    def test_neutral_groundedness_not_counted_as_ungrounded(self):
        # Q&A 形式ソース等で全主張 neutral（decided=0）→ support_rate=0.0 でも
        # 「根拠なし」ではなく「判定不能」。ungrounded の分子には入れず、
        # groundedness_neutral_rate で可視化する（過大計上の是正）。
        results = [
            rec("in-scope", "answer", "answer", citations=2,
                groundedness=0.0, decided=0),
            rec("in-scope", "answer", "answer", citations=1,
                groundedness=0.9, decided=3),
        ]
        m = compute_metrics(results, confirm_th=CONFIRM_TH)
        assert m["ungrounded_answer_rate"] == 0.0
        assert m["groundedness_neutral_rate"] == 0.5

    def test_low_support_with_decided_claims_still_ungrounded(self):
        # 判定できた主張がある（decided>0）のに支持率が低い → 真の「根拠なし」
        results = [
            rec("in-scope", "answer", "answer", citations=1,
                groundedness=0.2, decided=2),
        ]
        m = compute_metrics(results, confirm_th=CONFIRM_TH)
        assert m["ungrounded_answer_rate"] == 1.0
        assert m["groundedness_neutral_rate"] == 0.0

    def test_errors_are_excluded_from_rates(self):
        results = [
            rec("in-scope", "answer", "answer", citations=1, groundedness=0.9),
            rec("in-scope", "answer", None, error="ConnectionError: qdrant down"),
        ]
        m = compute_metrics(results, confirm_th=CONFIRM_TH)
        assert m["samples"] == 2 and m["errors"] == 1
        assert m["decision_accuracy"] == 1.0  # 実行成功分のみで算出

    def test_identity_check_violation_detected(self):
        results = [
            rec("action", "answer", "answer", expected_action="create_ticket",
                action="create_ticket", citations=1, groundedness=0.9,
                identity=False, expect_identity=True),
        ]
        m = compute_metrics(results, confirm_th=CONFIRM_TH)
        assert m["identity_check_rate"] == 0.0

    def test_format_table_renders_all_metrics(self):
        m = compute_metrics(
            [rec("in-scope", "answer", "answer", citations=1, groundedness=0.9)],
            confirm_th=CONFIRM_TH,
        )
        table = format_table(m)
        assert "decision_accuracy" in table
        assert "forced_escalate_misfire_rate" in table
        assert "groundedness_neutral_rate" in table
        assert "in-scope" in table


class TestCaseFiles:
    """cases/*.jsonl の妥当性（スキーマ・カテゴリ・カバレッジ）を CI で検証する。"""

    def test_case_files_exist_for_all_verticals(self):
        assert {p.stem for p in CASES_DIR.glob("*.jsonl")} == {"gov", "saas", "ec"}

    def test_cases_are_valid_and_labeled(self):
        for path in sorted(CASES_DIR.glob("*.jsonl")):
            cases = load_cases(path)
            assert cases, f"{path.name} が空"
            categories = set()
            for case in cases:
                assert case["vertical"] == path.stem
                assert case["category"] in CATEGORIES
                assert case["query"]
                assert case["expected_decision"] in ("answer", "escalate")
                assert "expected_action" in case
                categories.add(case["category"])
            # 5 カテゴリ（keyword-trap 含む）を各業界でカバーしていること
            assert set(CATEGORIES) <= categories, (
                f"{path.name}: カテゴリ不足 {set(CATEGORIES) - categories}"
            )

    def test_comment_lines_are_skipped(self):
        raw = Path(CASES_DIR / "gov.jsonl").read_text(encoding="utf-8")
        assert raw.lstrip().startswith("#")  # 先頭コメント行がある前提の検査
        for case in load_cases(CASES_DIR / "gov.jsonl"):
            json.dumps(case)  # すべて JSON 化可能
