# tests/test_review_rule_query_retrieve.py
"""② Retrieve をセグメント本文ではなくルール自身で検索することを固定するテスト。

## 回帰（実測 2026-09-30「シミが治る」LP・yakki-02 / yakki-04 に第 66 条を登録した後）

キーワード型ルール（セグメントスコープ）は、**セグメント本文（広告の文）**を検索クエリに
していた。規程コレクション `ec_ad_rules_anthropic` は「1 ルール = 1 行（要旨＋条文）」
なので、広告の文とはスコアが下限 0.70 に届かない。

    s001 最上位 0.6690 < 0.70 → 条文フォールバックを使います
    s002 最上位 0.6784 < 0.70 → 条文フォールバックを使います

登録した条文は ③ Detect にも ④ Ground にも一度も渡らず、根拠は要旨のままだった。
さらに、本文クエリの結果は**セグメント内の全候補ルールで共用**されていたので、
閾値を越えたとしても別ルールの行が根拠になりうる。

ここでは「ルール自身の文（`RuleItem.retrieval_query()`）で引いたときだけ自分の行が
高スコアで返り、広告の文では 0.67 しか出ない」規程コレクションをスタブで再現する。

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.core.review_agent import run_review_agent_core
from backend.app.core.rulesets import get_ruleset

# 3 段落 = 3 セグメント。yakki-02 は s001 と s003 の両方で候補になる。
DOCUMENT = (
    "使い続ければシミが治ると評判です。\n\n"
    "副作用がないので誰でも安心してお使いいただけます。\n\n"
    "シミもシワも目立たなくなります。"
)

EC_AD = get_ruleset("ec_ad")


def _statute(rule_id: str) -> str:
    return f"【条文】{rule_id} の条文本文"


def _row(rule, score: float) -> dict:
    """登録済みの規程 1 行（`qa_output/ec_ad_rules.csv` と同じ question / answer の形）。"""
    return {
        "score": score,
        "payload": {
            "question": f"{rule.law} {rule.article}（{rule.title}）",
            "answer": f"{rule.public_description()}\n\n{_statute(rule.rule_id)}",
        },
    }


class _Collection:
    """ルール自身の文でだけ自分の行が当たる規程コレクション（実測の再現）。"""

    def __init__(self, low_rules=(), text_hit=None):
        self.queries: list = []
        self._by_query = {rule.retrieval_query(): rule for rule in EC_AD.rules}
        self._low = set(low_rules)
        # 広告の文でも下限を越えて当たる行（越境の再現用）。None なら実測どおり 0.67
        self._text_hit = text_hit

    def execute(self, name, **kwargs):
        if name != "rag_search":
            return SimpleNamespace(success=False, output=None)
        query = kwargs["query"]
        self.queries.append(query)
        rule = self._by_query.get(query)
        if rule is not None and rule.rule_id not in self._low:
            output = [_row(rule, 0.90)]
        elif rule is None and self._text_hit is not None:
            output = [_row(EC_AD.rule_by_id(self._text_hit), 0.75)]
        else:
            # 広告の文（またはスコアの出ないルール）: 実測どおり下限 0.70 を割る
            anyone = rule or EC_AD.rule_by_id("keihyo-01")
            output = [_row(anyone, 0.67)]
        return SimpleNamespace(success=True, output=output)


@pytest.fixture
def collection(review_stub, monkeypatch):
    col = _Collection()
    monkeypatch.setattr(
        "backend.app.core.review_agent.create_tool_registry", lambda _c: col
    )
    return col


def _detect_evidence(stub, rule_id: str) -> list:
    return [ev for _text, rid, ev in stub.detect_calls if rid == rule_id]


def _ground_sources(stub, rule_id: str) -> list:
    title = EC_AD.rule_by_id(rule_id).title
    return [src for query, _msg, src in stub.verify_calls if f"「{title}」" in query]


class TestSegmentRulesUseTheirOwnQuery:

    def test_statute_reaches_detect_and_ground(self, review_stub, collection):
        """登録した条文が ③ Detect と ④ Ground に渡ること（本題）。"""
        run_review_agent_core(DOCUMENT)

        for rule_id in ("yakki-02", "yakki-04"):
            evidences = _detect_evidence(review_stub, rule_id)
            assert evidences, f"{rule_id} が判定されていない"
            assert all(_statute(rule_id) in ev for ev in evidences), (
                f"{rule_id}: 条文が ③ Detect に渡っていない（要旨のフォールバックのまま）"
            )
            sources = _ground_sources(review_stub, rule_id)
            assert sources, f"{rule_id} が ④ Ground に進んでいない"
            assert all(any(_statute(rule_id) in s for s in srcs) for srcs in sources), (
                f"{rule_id}: 条文が ④ Ground に渡っていない"
            )

    def test_no_evidence_from_another_rule(self, review_stub, monkeypatch):
        """広告の文に別ルールの行が当たっても、それを根拠にしないこと（越境しない）。

        本文クエリの結果はセグメント内の全候補ルールで共用されていたので、
        yakki-02 の行が当たると yakki-04 の判定の根拠にもなっていた。
        """
        col = _Collection(text_hit="yakki-02")
        monkeypatch.setattr(
            "backend.app.core.review_agent.create_tool_registry", lambda _c: col
        )
        run_review_agent_core(DOCUMENT)

        evidences = _detect_evidence(review_stub, "yakki-04")
        assert evidences
        for ev in evidences:
            assert _statute("yakki-02") not in ev, "別ルール（yakki-02）の条文が根拠になっている"
            assert _statute("yakki-04") in ev

    def test_segment_text_is_not_used_as_query(self, review_stub, collection):
        result = run_review_agent_core(DOCUMENT)

        texts = {segment.text for segment in result.segments}
        assert texts
        assert not texts & set(collection.queries), "セグメント本文を検索クエリにしている"

    def test_one_search_per_rule(self, review_stub, collection):
        """同じルールが複数のセグメントに出ても検索は 1 回。"""
        run_review_agent_core(DOCUMENT)

        yakki_02 = EC_AD.rule_by_id("yakki-02")
        assert collection.queries.count(yakki_02.retrieval_query()) == 1
        assert len(collection.queries) == len(set(collection.queries))
        # yakki-02 が本当に 2 つのセグメントで判定されていること（前提の確認）
        assert len(_detect_evidence(review_stub, "yakki-02")) == 2


class TestDropLogNamesTheRule:

    def test_fallback_log_says_which_rule(self, review_stub, monkeypatch):
        """条文フォールバックになったルールがログから分かること。"""
        col = _Collection(low_rules={"yakki-04"})
        monkeypatch.setattr(
            "backend.app.core.review_agent.create_tool_registry", lambda _c: col
        )
        logs = []
        run_review_agent_core(DOCUMENT, emit=lambda ev: logs.append(ev.message or ""))

        assert any("[retrieve] yakki-04: 関連度が低い規程を根拠にしません" in m
                   for m in logs), [m for m in logs if "[retrieve]" in m]
        # フォールバックは要旨（条文ではない）
        for ev in _detect_evidence(review_stub, "yakki-04"):
            assert _statute("yakki-04") not in ev
