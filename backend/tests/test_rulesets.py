# backend/tests/test_rulesets.py
"""GRACE-Review の RuleSet 定義（backend/app/core/rulesets.py）の整合性テスト。

設計: backend/docs/review_agent_spec.md §5。LLM・Qdrant・API キーは不要。

ここで固定しているのは「ルール定義が後段の二段判定と矛盾しないこと」である。
たとえば `always_check=True` のルールに keywords を書いてしまうと、第1段の
キーワード検出と第2段の常時実行が二重に効いて挙動が読めなくなるため、
そのような定義ミスをテストで落とす。

テストデータ（backend/tests/data/ec_ad_*.txt）についても、後続 STEP で
「NG は検出できる / OK は過検知しない / edge は抑止できる」を検証するための
前提が成立しているか（＝狙った文言が入っているか）をここで確かめておく。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.rulesets import (
    DEFAULT_CONFIRM_TH,
    DEFAULT_NOTIFY_TH,
    EC_AD,
    RULESETS,
    RuleItem,
    RuleSet,
    get_ruleset,
)

DATA_DIR = Path(__file__).parent / "data"

# 法律ごとの想定件数（設計書 §5.3）。増減させたら設計書も直すこと。
EXPECTED_LAW_COUNTS = {
    "景品表示法": 12,
    "医薬品医療機器等法": 3,
    "特定商取引法": 6,
}
EXPECTED_TOTAL = sum(EXPECTED_LAW_COUNTS.values())


# =============================================================================
# RuleSet / RuleItem の構造
# =============================================================================

def test_ec_ad_is_registered():
    """RULESETS から ec_ad を引ける。"""
    assert RULESETS["ec_ad"] is EC_AD
    assert isinstance(EC_AD, RuleSet)


def test_rule_count_matches_spec():
    """ルール総数が設計書の 21 件と一致する。"""
    assert len(EC_AD.rules) == EXPECTED_TOTAL


@pytest.mark.parametrize("law,expected", EXPECTED_LAW_COUNTS.items())
def test_rule_count_per_law(law: str, expected: int):
    """法律ごとの件数が設計書と一致する。"""
    assert sum(1 for r in EC_AD.rules if r.law == law) == expected


def test_rule_ids_are_unique():
    """rule_id は RuleSet 内で一意（指摘の識別子になるため重複は許さない）。"""
    ids = [r.rule_id for r in EC_AD.rules]
    assert len(ids) == len(set(ids)), f"重複: {[i for i in ids if ids.count(i) > 1]}"


def test_required_fields_are_filled():
    """表示・判定に使う必須項目が空でない。"""
    for rule in EC_AD.rules:
        assert rule.rule_id, "rule_id が空"
        assert rule.title, f"{rule.rule_id}: title が空"
        assert rule.category, f"{rule.rule_id}: category が空"
        assert rule.law, f"{rule.rule_id}: law が空"
        assert rule.article, f"{rule.rule_id}: article が空"
        # description は LLM プロンプトと根拠フォールバックを兼ねるため実質的な長さを要求
        assert len(rule.description) >= 40, f"{rule.rule_id}: description が短すぎる"


def test_severity_default_is_valid():
    """severity_default は high/medium/low のいずれか。"""
    for rule in EC_AD.rules:
        assert rule.severity_default in ("high", "medium", "low"), rule.rule_id


# =============================================================================
# 二段判定との整合（ここが定義ミスを落とす本体）
# =============================================================================

def test_tokusho_rules_are_always_check():
    """特商法の表記漏れ検出はキーワードでは拾えないため、全件 always_check。"""
    tokusho = [r for r in EC_AD.rules if r.law == "特定商取引法"]
    assert tokusho, "特商法ルールが 1 件も無い"
    for rule in tokusho:
        assert rule.always_check is True, f"{rule.rule_id}: always_check であるべき"


def test_always_check_rules_have_no_keywords():
    """always_check のルールに keywords を持たせない（第1段と二重に効くため）。"""
    for rule in EC_AD.always_check_rules:
        assert not rule.keywords, (
            f"{rule.rule_id}: always_check=True なら keywords は不要"
        )


def test_keyword_rules_have_keywords():
    """always_check でないルールは keywords が必須（第1段を通過できないため）。"""
    for rule in EC_AD.keyword_rules:
        assert rule.keywords, (
            f"{rule.rule_id}: always_check=False なら keywords が必要"
            "（無いと第2段へ到達せず永久に検出されない）"
        )


def test_rule_partition_is_exhaustive():
    """always_check_rules と keyword_rules で全ルールを漏れなく二分する。"""
    assert len(EC_AD.always_check_rules) + len(EC_AD.keyword_rules) == len(EC_AD.rules)


def test_keywords_have_no_empty_string():
    """空文字の keyword は全セグメントに一致してしまうため許さない。"""
    for rule in EC_AD.rules:
        for keyword in rule.keywords:
            assert keyword.strip(), f"{rule.rule_id}: 空の keyword がある"


# =============================================================================
# しきい値・アクション・プロンプト
# =============================================================================

def test_thresholds_are_ordered():
    """notify_th > confirm_th（自動確定のほうが厳しい）。"""
    assert EC_AD.notify_th > EC_AD.confirm_th
    assert 0.0 < EC_AD.confirm_th < 1.0
    assert 0.0 < EC_AD.notify_th <= 1.0


def test_ec_ad_thresholds_are_stricter_than_default():
    """法令チェックは誤指摘のコストが高いため、既定より緩めない。"""
    assert EC_AD.notify_th >= DEFAULT_NOTIFY_TH
    assert EC_AD.confirm_th >= DEFAULT_CONFIRM_TH


def test_critical_keywords_are_defined():
    """強制 high 用の重大リスク語が定義されている。"""
    assert EC_AD.critical_keywords
    for keyword in EC_AD.critical_keywords:
        assert keyword.strip(), "空の critical_keyword がある"


def test_action_map_and_prompt_addendum():
    """⑦ Action の対応表と ③ Detect への方針注入が定義されている。"""
    assert EC_AD.action_map
    assert set(EC_AD.action_map.values()) <= {
        "create_ticket", "send_reply", "escalate_to_human",
    }
    assert EC_AD.prompt_addendum


# =============================================================================
# ヘルパー
# =============================================================================

def test_rule_by_id():
    """rule_id から RuleItem を引ける。未知の ID は None。"""
    rule = EC_AD.rule_by_id("keihyo-01")
    assert isinstance(rule, RuleItem)
    assert rule.title == "優良誤認表示"
    assert EC_AD.rule_by_id("does-not-exist") is None


def test_citation_contains_law_article_and_description():
    """④ Ground の根拠フォールバックに、条文特定に必要な情報が含まれる。"""
    rule = EC_AD.rule_by_id("keihyo-03")
    citation = rule.citation()
    assert rule.law in citation
    assert rule.article in citation
    assert rule.title in citation
    assert rule.description in citation


@pytest.mark.parametrize(
    "ruleset_id,expected",
    [("ec_ad", EC_AD), (None, None), ("", None), ("unknown", None)],
)
def test_get_ruleset(ruleset_id, expected):
    """未指定・未知の ID は None（既定しきい値で動作させる）。"""
    assert get_ruleset(ruleset_id) is expected


# =============================================================================
# テストデータの前提（後続 STEP の検証が成立するか）
# =============================================================================

@pytest.mark.parametrize(
    "filename", ["ec_ad_ng_sample.txt", "ec_ad_ok_sample.txt", "ec_ad_edge_sample.txt"]
)
def test_sample_files_exist_and_readable(filename: str):
    """テストデータ 3 本が存在し、空でない。"""
    path = DATA_DIR / filename
    assert path.exists(), f"{path} が無い"
    assert path.read_text(encoding="utf-8").strip(), f"{path} が空"


def test_ng_sample_contains_detectable_violations():
    """NG サンプルに、検出対象となる keyword が十分な種類だけ含まれる。

    「検出できるはず」の前提が崩れていないかを確認する（実際に検出できるかは
    STEP4 の test_review_agent_core.py が担当する）。
    """
    text = (DATA_DIR / "ec_ad_ng_sample.txt").read_text(encoding="utf-8")
    hit_rules = [
        r.rule_id for r in EC_AD.keyword_rules
        if any(k in text for k in r.keywords)
    ]
    # 景表法・薬機法の広い範囲に当たること（設計書の想定 12 指摘に対する下限）
    assert len(hit_rules) >= 8, f"NG サンプルの当たりが少なすぎる: {hit_rules}"


def test_ng_sample_contains_critical_keyword():
    """NG サンプルに重大リスク語が含まれる（強制 high のテストに使うため）。"""
    text = (DATA_DIR / "ec_ad_ng_sample.txt").read_text(encoding="utf-8")
    assert any(k in text for k in EC_AD.critical_keywords)


def test_ok_sample_has_no_critical_keyword():
    """OK サンプルに重大リスク語が無い（過検知テストの前提）。"""
    text = (DATA_DIR / "ec_ad_ok_sample.txt").read_text(encoding="utf-8")
    hits = [k for k in EC_AD.critical_keywords if k in text]
    assert not hits, f"OK サンプルに重大リスク語が混入している: {hits}"


def test_ok_sample_satisfies_tokusho_items():
    """OK サンプルが特商法の表記項目を満たす（表記漏れで指摘されない前提）。"""
    text = (DATA_DIR / "ec_ad_ok_sample.txt").read_text(encoding="utf-8")
    required = {
        "販売価格": ["円"],
        "送料": ["送料"],
        "支払方法": ["お支払い方法", "クレジットカード"],
        "引渡時期": ["お届け", "発送"],
        "返品特約": ["返品"],
        "事業者情報": ["販売業者", "所在地", "電話番号"],
    }
    for item, needles in required.items():
        assert any(n in text for n in needles), f"OK サンプルに {item} の記載が無い"


def test_edge_sample_contains_negated_critical_keywords():
    """edge サンプルに「否定文脈の重大リスク語」が含まれる（抑止テストの前提）。

    「当社は No.1 という表現を使用しません」型の文。第1段のキーワードには
    当たるが、第2段の意図分類で誤検知として抑止されるべきケース。
    """
    text = (DATA_DIR / "ec_ad_edge_sample.txt").read_text(encoding="utf-8")
    hits = [k for k in EC_AD.critical_keywords if k in text]
    assert len(hits) >= 3, f"edge サンプルの重大リスク語が少なすぎる: {hits}"
    assert "使用しません" in text, "否定文脈であることが読み取れる文言が無い"
