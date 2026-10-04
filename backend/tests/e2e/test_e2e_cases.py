"""E2E のケース定義（`cases.py`）のテスト。**API を呼ばないので CI でも走る。**

E2E 本体は課金されるので普段は流れない。そのため「画面の例文が変わったのに
E2E の期待値が古いまま」というずれは、ここで CI に落としてもらう。
"""

import pytest

from backend.app.core.rulesets import RULESETS
from backend.app.core.verticals import PROFILES
from backend.tests.e2e import cases


def test_every_vertical_example_has_an_expectation():
    examples = cases.support_examples()

    assert set(examples) == set(cases.SUPPORT_EXPECT)
    assert set(examples) <= set(PROFILES)
    assert all(q.strip() for q in examples.values())


def test_every_review_example_has_an_expectation():
    examples = cases.review_examples()

    assert set(examples) == set(cases.REVIEW_EXPECT)
    assert all(len(doc) > 20 for doc in examples.values())


def test_review_documents_are_read_verbatim():
    """TS の文字列連結と \\n を正しく復元している（文面が変わると期待値の意味も変わる）。"""
    doc = cases.review_examples()["適正LP案"]

    assert doc.startswith("当社の美容液は、うるおいを与えて肌をなめらかに整えます。\n\n■ 特定商取引法に基づく表記\n")
    assert "返品: 商品到着後14日以内" in doc
    assert "'" not in doc and "\\n" not in doc


def test_expected_rule_ids_exist():
    known = {rule.rule_id for rule in RULESETS["ec_ad"].rules}

    for expect in cases.REVIEW_EXPECT.values():
        assert set(expect.get("rule_ids", [])) <= known


# ----------------------------------------------------------------------
# 事実チェック・範囲外の質問・記録だけの期待値（2026-10-04 追加）
# ----------------------------------------------------------------------
def test_fact_and_watch_tables_match_the_cases():
    assert set(cases.SUPPORT_FACTS) <= set(cases.SUPPORT_EXPECT)
    assert set(cases.REVIEW_WATCH) == set(cases.REVIEW_EXPECT)
    known = {rule.rule_id for rule in RULESETS["ec_ad"].rules}
    for rule_ids in cases.REVIEW_WATCH.values():
        assert set(rule_ids) <= known
    # 過検知を見る例文（指摘 0 件）に「出るはず」の指摘を書かない
    assert cases.REVIEW_WATCH["適正LP案"] == []


def test_out_of_scope_questions_are_not_screen_examples():
    examples = cases.support_examples()

    assert set(cases.OUT_OF_SCOPE) <= set(PROFILES)
    assert not set(cases.OUT_OF_SCOPE.values()) & set(examples.values())


@pytest.mark.parametrize("answer, fact, expected", [
    ("手数料は1通300円です。", "300円", True),
    ("手数料は1通３００円です。", "300円", True),           # 全角数字
    ("商品到着後 14 日以内", "14日", True),                  # 空白
    ("1,000円", "1000円", True),                            # 桁区切り
    ("ステータスページ（status.example.jp）", "status.example.jp", True),
    ("担当窓口へお問い合わせください。", "300円", False),
    (None, "300円", False),
])
def test_contains_fact_ignores_formatting_only(answer, fact, expected):
    assert cases.contains_fact(answer, fact) is expected


def test_summary_counts_runs_from_outcomes():
    """`record` まで届かなかった回（例外）も失敗 1 回として数える。skip は数えない。"""
    from backend.tests.e2e.conftest import summarize

    records = [
        {"test": "t[run1]", "elapsed_sec": 10.0, "findings": [{"rule_id": "a"}], "missing_expected": []},
        {"test": "t[run2]", "elapsed_sec": 20.0, "findings": [], "missing_expected": ["a"]},
    ]
    outcomes = {
        "t[run1]": {"case": "t", "outcome": "passed"},
        "t[run2]": {"case": "t", "outcome": "failed", "failure": "AssertionError: x"},
        "t[run3]": {"case": "t", "outcome": "failed", "failure": "RuntimeError: boom"},
        "u": {"case": "u", "outcome": "skipped"},
    }

    summary = summarize(records, outcomes)

    assert summary == {"t": {
        "runs": 3, "passed": 1, "pass_rate": 0.33, "avg_elapsed_sec": 15.0,
        "rule_id_rate": {"a": 0.33}, "missing_rate": {"a": 0.33},
        "failures": ["AssertionError: x", "RuntimeError: boom"],
    }}
