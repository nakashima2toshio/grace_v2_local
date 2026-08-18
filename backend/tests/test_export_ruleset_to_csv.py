# backend/tests/test_export_ruleset_to_csv.py
"""条文 CSV 書き出し（`scripts/export_ruleset_to_csv.py`）のテスト。

## 背景

`ec_ad` の検索スコープ `ec_ad_rules_anthropic` が未登録で、実行ログが
毎回こうなっていた（実測 2026-08-17 20:07 〜 2026-08-18 21:41）。

    doc/tokusho-01: 文書全体で判定 / 規程 0 件
    doc/tokusho-02: 文書全体で判定 / 規程 0 件      ← 7 ルール中 6 つが 0 件

指摘の「根拠」がすべて条文フォールバックになり、RAG 経路が一度も通っていない。

## ここで固定すること

書き出した CSV が**そのまま登録できる形**であること。特に、
`register_to_qdrant.py` と Review 側の読み取りが噛み合うこと。

  1. 列が `question` / `answer` / `topic`（`detect_text_column` が
     `question` + `answer` を埋め込み対象として自動検出する形）
  2. 全ルールが 1 行ずつ出ること（取りこぼさない）
  3. `question` が UI の引用ラベルとして読める形（法令 + 条 + タイトル）
  4. `answer` が ④ Ground へ渡る根拠本文（`RuleItem.description`）であること
  5. 検索クエリ `f"{rule.title} {rule.description}"` と語が重なること
     — これが `evidence_min_score`（0.70）を超えるための前提
  6. 未知の RuleSet ID はエラーにすること

⚠️ Qdrant にも Embedding にも接続しない。
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

from backend.app.core.rulesets import EC_AD

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_ruleset_to_csv.py"


def _load_module():
    """`scripts/` はパッケージではないのでファイルから直接読み込む。"""
    spec = importlib.util.spec_from_file_location("export_ruleset_to_csv", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exporter():
    return _load_module()


@pytest.fixture()
def written(exporter, tmp_path):
    output = tmp_path / "ec_ad_rules.csv"
    exporter.main(["--ruleset", "ec_ad", "--output", str(output)])
    with output.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# =============================================================================
# ① register_to_qdrant.py が読める形
# =============================================================================

class TestCsvShape:

    def test_columns_match_the_registrar(self, written):
        """`detect_text_column` は question + answer を自動検出する。"""
        assert written
        assert set(written[0]) == {"question", "answer", "topic"}

    def test_every_rule_is_exported(self, written):
        assert len(written) == len(EC_AD.rules)

    def test_no_empty_cells(self, written):
        for row in written:
            for key in ("question", "answer", "topic"):
                assert row[key].strip(), f"{key} が空: {row}"


# =============================================================================
# ② Review 側の読み取りと噛み合う
# =============================================================================

class TestFieldsMatchTheReviewSide:
    """`_retrieve_evidence` の読み取り:

        title = payload.get("title") or payload.get("question") or "(規程)"
        body  = payload.get("answer") or payload.get("text") or ""
    """

    def test_question_is_a_readable_citation_label(self, written):
        """UI に `[規程] 特定商取引法 第11条（販売価格・送料の明示）` と出る形。"""
        by_title = {r.title: r for r in EC_AD.rules}
        for row in written:
            assert row["question"].endswith("）")
            title = row["question"].rsplit("（", 1)[1][:-1]
            assert title in by_title, f"タイトルを復元できない: {row['question']}"
            rule = by_title[title]
            assert rule.law in row["question"]
            assert rule.article in row["question"]

    def test_answer_is_the_evidence_body(self, written):
        descriptions = {r.description for r in EC_AD.rules}
        for row in written:
            assert row["answer"] in descriptions

    def test_topic_carries_the_category(self, written):
        categories = {r.category for r in EC_AD.rules}
        for row in written:
            assert row["topic"] in categories


# =============================================================================
# ③ 検索が当たる前提（evidence_min_score = 0.70 を超えるため）
# =============================================================================

class TestRetrievabilityPremise:
    """② Retrieve の検索クエリは `f"{rule.title} {rule.description}"`。

    埋め込み対象は `question + "\\n" + answer` なので、両者の語が重なっていないと
    `RuleSet.evidence_min_score`（0.70）を超えられない。ここでは語の重なりを
    構造的に保証する（スコアそのものは Embedding が要るので測れない）。
    """

    def test_embedded_text_contains_the_query_terms(self, written):
        rows = {r["answer"]: r for r in written}
        for rule in EC_AD.rules:
            row = rows[rule.description]
            embedded = f"{row['question']}\n{row['answer']}"

            assert rule.title in embedded, f"{rule.rule_id}: タイトルが埋め込みに無い"
            assert rule.description in embedded, (
                f"{rule.rule_id}: description が埋め込みに無い"
            )

    def test_policy_rule_is_included(self, written):
        """policy-01（社内規程との整合）も書き出されること。

        ⚠️ ただし policy-01 が実際に機能するには**自社規程の実データ**が要る。
        条文の書き出しだけでは「8日 vs 14日」は検出できない。
        """
        assert any("表示内容と社内規程の不一致" in r["question"] for r in written)


# =============================================================================
# ④ 異常系
# =============================================================================

class TestErrors:

    def test_unknown_ruleset_is_rejected(self, exporter, tmp_path):
        with pytest.raises(SystemExit) as excinfo:
            exporter.main(["--ruleset", "no_such", "--output", str(tmp_path / "x.csv")])

        assert "unknown ruleset" in str(excinfo.value)

    def test_output_directory_is_created(self, exporter, tmp_path):
        output = tmp_path / "nested" / "dir" / "rules.csv"

        exporter.main(["--ruleset", "ec_ad", "--output", str(output)])

        assert output.exists()
