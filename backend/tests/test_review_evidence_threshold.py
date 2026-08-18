# backend/tests/test_review_evidence_threshold.py
"""**関連度の低い規程を根拠にしない**ことを固定するテスト。

## 背景（実測 2026-08-17 20:07 / GRACE-Review）

S1 のログが問題を明示していた。

    検索スコープ: ec_ad_rules_anthropic, ec_policy_anthropic
    （未登録コレクションは条文フォールバックを使用）

**条文コレクション `ec_ad_rules_anthropic` は未登録。** 実在するのは
`ec_policy_anthropic` で、その中身は返品・返金・交換・キャンセル・領収書の FAQ だけ。
**特商法の条文は 1 件も入っていない。** それでも緩和閾値 0.5 で 5 件拾い、そのまま
根拠として表示していた。

    指摘: 販売価格・送料の明示（特定商取引法 第11条）
    根拠: [規程] 返品規定を教えてください,
          [規程] 不良品が届いた場合の対応を教えてください,
          [規程] 返金ポリシーを教えてください,
          [規程] 返品できない商品はありますか？,
          [規程] 交換の条件を教えてください          ← 全部無関係

しかも呼び出し側は `citations or [rule.citation()]` で分岐するため、**1 件でも
拾えば正しい条文フォールバックが低スコアの FAQ に上書きされる**。
「条文つきの指摘を出します」という機能の価値が崩れていた。

さらに検証まで汚染していた（20:09:25）。

    WARNING [groundedness] contradicted: 対象テキストには送料に関する記載が一切ない

指摘自体は事実（送料の記載は無い）だが、無関係な返品 FAQ と照合されたため
矛盾判定になっていた。

## なぜ Support より高い閾値にするのか

Support の `executor.reasoning_min_rag_score` は 0.64。Review では規程・条文が
一次情報であり、無関係な規程を根拠に載せる害が大きい。

`DEFAULT_EVIDENCE_MIN_SCORE = 0.70` は `agent_tools.COSINE_SIMILARITY_THRESHOLD`
（RAG の一次閾値）と同じ値である。つまり **Review は緩和閾値（0.5）でしか拾えな
かった結果を根拠にしない**。一次閾値に届かなければ `RuleItem.description`
（条文フォールバック）を使う方が正確なので、無理に拾う理由が無い。

実測の裏付け:

    採用されてしまった無関係な返品 FAQ の Top … 0.6581 / 0.6737 / 0.6765 / 0.6847
    規程が本当に関連していた唯一のケース   … 0.7914（返品期間 8日 vs 14日 の照合）
    → 0.70 で分離できる（マージン 0.1067。Support の 0.046 よりはるかに広い）

ここで固定すること:
  1. 閾値未満の規程を根拠にしないこと
  2. 落とした結果は条文フォールバックへ（`citations` を空で返す）
  3. 閾値以上の規程は従来どおり採用すること（見落としを作らない）
  4. 落としたことが**実行ログに出る**こと（画面から追える）
  5. score を持たない結果は従来どおり通すこと（後方互換）
  6. RuleSet 側で閾値を上書きできること

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from backend.app.core.review_agent import _retrieve_evidence, run_review_agent_core
from backend.app.core.rulesets import (
    DEFAULT_EVIDENCE_MIN_SCORE,
    RuleSet,
    get_ruleset,
)

# 実測で採用されてしまった無関係な返品 FAQ（スコアも実測値）。
IRRELEVANT = [
    {"score": 0.6847, "payload": {"question": "返品規定を教えてください",
                                  "answer": "商品到着後14日以内かつ未使用・未開封に限り返品を承ります。"}},
    {"score": 0.6737, "payload": {"question": "不良品が届いた場合の対応を教えてください",
                                  "answer": "商品到着後7日以内にご連絡ください。"}},
    {"score": 0.6581, "payload": {"question": "返金ポリシーを教えてください",
                                  "answer": "到着確認後 5 営業日以内に返金します。"}},
]
# 実測で規程が本当に関連していた唯一のケース。
RELEVANT = [
    {"score": 0.7914, "payload": {"question": "返品規定を教えてください",
                                  "answer": "商品到着後14日以内かつ未使用・未開封に限り返品を承ります。"}},
]


def _tool(output):
    """rag_search が `output` を返すツールレジストリのスタブ。"""
    class _Registry:
        def execute(self, _name, **_kwargs):
            class _Res:
                success = True
            res = _Res()
            res.output = output
            return res
    return _Registry()


def _ruleset(**overrides) -> RuleSet:
    rs = get_ruleset("ec_ad")
    fields = {
        "id": rs.id, "name": rs.name, "collections": list(rs.collections),
        "rules": list(rs.rules), "critical_keywords": list(rs.critical_keywords),
    }
    fields.update(overrides)
    return RuleSet(**fields)


# =============================================================================
# ① 閾値で落とす
# =============================================================================

class TestLowScoreEvidenceIsDropped:

    def test_irrelevant_evidence_is_not_cited(self):
        """実測の再現: 0.65〜0.68 の返品 FAQ を根拠にしない。"""
        citations, sources = _retrieve_evidence(
            _tool(IRRELEVANT), "販売価格・送料の明示", _ruleset(),
        )

        assert citations == [], (
            f"関連度の低い規程を根拠にしている: {citations}"
        )
        assert sources == [], "検証（④ Ground）へも渡してはいけない"

    def test_relevant_evidence_is_kept(self):
        """閾値以上は従来どおり採用する（見落としを作らない）。"""
        citations, sources = _retrieve_evidence(
            _tool(RELEVANT), "返品特約の表示", _ruleset(),
        )

        assert citations == ["[規程] 返品規定を教えてください"]
        assert len(sources) == 1

    def test_mixed_keeps_only_the_relevant_ones(self):
        citations, _sources = _retrieve_evidence(
            _tool(RELEVANT + IRRELEVANT), "返品特約の表示", _ruleset(),
        )

        assert citations == ["[規程] 返品規定を教えてください"]

    def test_entries_without_score_are_kept(self):
        """score を持たない結果は従来どおり通す（後方互換）。"""
        citations, sources = _retrieve_evidence(
            _tool([{"payload": {"title": "特定商取引法 第11条", "answer": "本文"}}]),
            "販売価格", _ruleset(),
        )

        assert citations == ["[規程] 特定商取引法 第11条"]
        assert sources == ["本文"]

    def test_threshold_is_overridable_per_ruleset(self):
        """RuleSet 側で閾値を下げれば従来の挙動に戻せる。"""
        citations, _sources = _retrieve_evidence(
            _tool(IRRELEVANT), "販売価格", _ruleset(evidence_min_score=0.5),
        )

        assert len(citations) == 3

    def test_default_is_higher_than_support(self):
        """Support の 0.64 より高いこと（規程は一次情報のため）。"""
        assert DEFAULT_EVIDENCE_MIN_SCORE > 0.64
        assert _ruleset().evidence_min_score == DEFAULT_EVIDENCE_MIN_SCORE


# =============================================================================
# ② 落としたら条文フォールバックへ
# =============================================================================

class TestFallsBackToTheStatute:
    """⚠️ **呼び出し側の `citations or [rule.citation()]` が要点。**

    低スコアの規程を 1 件でも返すと、正しい条文フォールバックがそれに
    上書きされる。空で返すことで条文が使われる。
    """

    def test_statute_citation_is_used_when_all_evidence_is_dropped(self, review_stub):
        review_stub.rag_output = IRRELEVANT

        result = run_review_agent_core("業界No.1の品質です。")

        assert result.findings
        for finding in result.findings:
            assert finding.citations, "根拠が空になっている"
            for citation in finding.citations:
                assert "返品規定を教えてください" not in citation, (
                    "無関係な返品 FAQ が根拠として残っている"
                )
            # 条文フォールバックは「[規程] <法令> <条> (<タイトル>)」の形
            assert any(finding.law in c for c in finding.citations), (
                f"条文フォールバックが使われていない: {finding.citations}"
            )

    def test_relevant_evidence_still_wins_over_the_statute(self, review_stub):
        review_stub.rag_output = RELEVANT

        result = run_review_agent_core("業界No.1の品質です。")

        assert result.findings
        assert result.findings[0].citations == ["[規程] 返品規定を教えてください"]


# =============================================================================
# ③ 落としたことが実行ログに出る
# =============================================================================

class TestDropIsVisibleInTheLog:
    """⚠️ **画面から追えることが要件。** Python の logger だけに出すと、
    「なぜ根拠が条文フォールバックになったのか」を UI・SSE から追えない
    （#74 で ④' の判定失敗理由について学んだのと同じ話）。
    """

    def test_on_drop_reports_titles_and_scores(self):
        messages: list[str] = []

        _retrieve_evidence(
            _tool(IRRELEVANT), "販売価格", _ruleset(),
            on_drop=messages.append,
        )

        [message] = messages
        assert "返品規定を教えてください" in message
        assert "0.6847" in message, "スコアが分からないと閾値の妥当性を判断できない"
        assert "0.70" in message, "どの閾値で落としたのかが分からない"
        assert "条文フォールバック" in message

    def test_no_message_when_nothing_is_dropped(self):
        messages: list[str] = []

        _retrieve_evidence(
            _tool(RELEVANT), "返品特約", _ruleset(), on_drop=messages.append,
        )

        assert messages == []

    def test_drop_reaches_the_pipeline_log(self, review_stub):
        review_stub.rag_output = IRRELEVANT
        events = []

        run_review_agent_core("業界No.1の品質です。", emit=events.append)

        logs = [e.message for e in events if e.type == "log" and e.step == "retrieve"]
        assert any("関連度が低い規程を根拠にしません" in m for m in logs), (
            f"実行ログに出ていない: {logs}"
        )
