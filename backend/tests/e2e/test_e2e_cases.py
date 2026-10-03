"""E2E のケース定義（`cases.py`）のテスト。**API を呼ばないので CI でも走る。**

E2E 本体は課金されるので普段は流れない。そのため「画面の例文が変わったのに
E2E の期待値が古いまま」というずれは、ここで CI に落としてもらう。
"""

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
