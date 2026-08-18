# backend/tests/test_review_document_scope.py
"""**表記漏れは文書全体で判定する**ことを固定するテスト。

## 背景（実測 2026-08-17 20:07 / GRACE-Review）

法定表示事項をすべて満たした「適正LP案」を点検させたところ、**11 件（重大 7 件）**
の指摘が出た。正しい指摘は 3 件（送料・支払方法・引渡時期の記載漏れ）で、
**8 件が誤検知**だった。

    該当箇所「当社の美容液は、うるおいを与えて肌をなめらかに整えます。」
      → 「事業者の氏名（名称）、住所、電話番号…が一切含まれていません」
         （実際は同じ文書の 3〜6 行目にすべて記載されている）

    該当箇所「■ 特定商取引法に基づく表記」
      → 「販売価格および送料の具体的な記載が一切ありません」
         （販売価格は 7 行目に記載されている）

    該当箇所「所在地: 東京都千代田区1-1-1」
      → 「事業者の氏名および電話番号の記載が確認できず」
         （3 行目・6 行目に記載されている）

## 原因

`always_check`（表記漏れ）のルールが**毎セグメントの候補**に入っており、
判定 LLM には `detect(segment.text, ...)` で**セグメント 1 行だけ**が
「対象テキスト」として渡っていた。「見出しの行に会社名が書いていない」のは
当たり前で、LLM は与えられた 1 行について正直に答えているだけ。
**判定の入力スコープが誤っていた。**

コードのコメントは元から文書全体を意図していた（`rulesets.py`）。

    # 表記漏れの検出はキーワードでは拾えないため、文書全体に対して常時チェックする。

`select_candidate_rules` の docstring も「表記が『無い』ことの検出はキーワード
一致では原理的に不可能」と書いていたが、**同じ理屈はセグメント単位の判定にも
当てはまる**。1 行を見て「文書に無い」とは言えない。

## 副次的な効果（コスト・打ち切り）

判定回数が「セグメント数 × 常時ルール数」から「常時ルール数」へ落ちる。
実測では 8 セグメント × 6 ルール = 48 回 → **6 回**。

これは `MAX_LLM_CALLS = 300` の打ち切りにも効く。常時 6 ルールだと
300 ÷ 6 = 50 セグメントで打ち切られるため、**UI が 50,000 文字を受け付けるのに
実効上限は約 1,000 文字**（実測 20 文字/セグメント）で、それを超えると
静かに truncate された結果が「点検完了」として表示されていた。

ここで固定すること:
  1. 別の行に記載がある事項を「無い」と指摘しないこと（実測の再現）
  2. 表記漏れルールは文書全体を 1 回だけ判定すること
  3. キーワード型ルールは従来どおりセグメント単位で判定すること
  4. 判定回数がセグメント数に比例しないこと
  5. 文書全体スコープの指摘は原文をハイライトしないこと（空スパン）
  6. 規程の検索クエリがルール自身であること

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from backend.app.core.review_agent import (
    DOCUMENT_SEGMENT_ID,
    run_review_agent_core,
)
from backend.app.core.review_gates import DetectVerdict
from backend.app.core.rulesets import get_ruleset

# 実測で使われた「適正LP案」。法定表示事項は 3〜8 行目に揃っている。
GOOD_LP = """当社の美容液は、うるおいを与えて肌をなめらかに整えます。
■ 特定商取引法に基づく表記
販売業者: 株式会社サンプル
運営責任者: 山田太郎
所在地: 東京都千代田区1-1-1
電話番号: 03-0000-0000
販売価格: 4,980円（税込）
返品: 商品到着後8日以内、未開封に限り返品可能（送料はお客様負担）"""

# 各表記漏れルールが「書かれている」と判断する手掛かり語。
# 判定 LLM の代役として、対象テキストにこの語があれば違反ではないとする。
PRESENCE_MARKERS = {
    "tokusho-01": "販売価格",
    "tokusho-04": "返品",
    "tokusho-05": "販売業者",
}


def _honest_detect(text, rule, _evidence):
    """判定 LLM の**正直な**代役。

    「手掛かり語が対象テキストに無ければ、その表記は無い」と答える。実際の LLM も
    与えられた対象テキストだけを見てこう答えていた。したがって、この代役で
    誤検知が出るなら**渡しているテキストが間違っている**。
    """
    marker = PRESENCE_MARKERS.get(rule.rule_id)
    if marker is None:
        return DetectVerdict(violates=False)
    if marker in text:
        return DetectVerdict(violates=False)
    return DetectVerdict(
        violates=True,
        message=f"{rule.title}の記載が一切ありません",
        suggestion=f"{rule.title}を明記してください",
        excerpt="",
    )


def _always_check_ids():
    return {r.rule_id for r in get_ruleset("ec_ad").always_check_rules}


# =============================================================================
# ① 実測の再現 — 別の行にある事項を「無い」と指摘しない
# =============================================================================

class TestNoFalsePositiveForPresentDisclosures:

    def test_present_disclosures_are_not_flagged(self, review_stub):
        """文書のどこかに記載があれば指摘しない（実測 8 件の誤検知の再現）。"""
        review_stub.detect = _honest_detect

        result = run_review_agent_core(GOOD_LP)

        flagged = {f.rule_id for f in result.findings}
        for rule_id, marker in PRESENCE_MARKERS.items():
            assert rule_id not in flagged, (
                f"{rule_id} を誤検知している。「{marker}」は文書に記載されているのに"
                "「一切ありません」と指摘した（セグメント単位で判定している）"
            )

    def test_missing_disclosures_are_still_flagged(self, review_stub):
        """記載が本当に無いものは従来どおり指摘する（見落としを作らない）。"""
        review_stub.detect = _honest_detect
        # 「返品」の記載を落とした文書
        document = GOOD_LP.rsplit("\n", 1)[0]

        result = run_review_agent_core(document)

        assert "tokusho-04" in {f.rule_id for f in result.findings}, (
            "返品特約の記載が無いのに指摘されていない"
        )

    def test_each_always_check_rule_is_judged_once(self, review_stub):
        """同じルールが複数回判定されない（重複指摘の土台を作らない）。"""
        review_stub.detect = _honest_detect

        run_review_agent_core(GOOD_LP)

        judged = [rid for _t, rid, _e in review_stub.detect_calls
                  if rid in _always_check_ids()]
        assert sorted(judged) == sorted(set(judged)), (
            f"表記漏れルールが複数回判定されている: {judged}"
        )


# =============================================================================
# ② 判定単位 — 何を渡しているか
# =============================================================================

class TestJudgementScope:

    def _calls(self, stub):
        return {rid: text for text, rid, _e in stub.detect_calls}

    def test_always_check_receives_the_whole_document(self, review_stub):
        """表記漏れルールの対象テキストは**文書全体**。"""
        run_review_agent_core(GOOD_LP)

        calls = self._calls(review_stub)
        for rule_id in _always_check_ids():
            assert calls.get(rule_id) == GOOD_LP, (
                f"{rule_id} にセグメントを渡している（文書全体でなければ"
                "「無い」ことは判定できない）"
            )

    def test_keyword_rules_receive_the_segment(self, review_stub):
        """キーワード型ルールは従来どおりセグメント単位。"""
        document = "業界No.1の品質です。\n\n当社の美容液です。"
        run_review_agent_core(document)

        keyword_calls = [(t, rid) for t, rid, _e in review_stub.detect_calls
                         if rid not in _always_check_ids()]
        assert keyword_calls, "キーワード型ルールが判定されていない"
        for text, rule_id in keyword_calls:
            assert text != document, (
                f"{rule_id} に文書全体を渡している（該当箇所を特定できない）"
            )
            assert text in document

    def test_call_count_does_not_scale_with_segments(self, review_stub):
        """判定回数がセグメント数に比例しないこと。

        以前は セグメント数 × 常時ルール数（実測 8 × 6 = 48）だった。
        """
        def _always_check_calls(stub):
            return [rid for _t, rid, _e in stub.detect_calls
                    if rid in _always_check_ids()]

        run_review_agent_core(GOOD_LP)
        few = len(_always_check_calls(review_stub))

        review_stub.detect_calls.clear()
        # 段落を増やす（表記漏れの判定回数は変わってはいけない）
        run_review_agent_core(GOOD_LP + ("\n\n追記の段落です。" * 12))
        many = len(_always_check_calls(review_stub))

        assert few == len(_always_check_ids()), f"想定外の判定回数: {few}"
        assert many == few, (
            f"セグメントを増やすと表記漏れの判定回数が増えている: {few} → {many} 回"
        )


# =============================================================================
# ③ 文書全体スコープの指摘の見え方
# =============================================================================

class TestDocumentScopeFinding:

    def _doc_findings(self, result):
        return [f for f in result.findings if f.segment_id == DOCUMENT_SEGMENT_ID]

    def test_document_findings_carry_the_sentinel_segment_id(self, review_stub):
        review_stub.detect = _honest_detect
        result = run_review_agent_core(GOOD_LP.rsplit("\n", 1)[0])

        assert self._doc_findings(result), "文書全体スコープの指摘が無い"

    def test_document_findings_do_not_highlight(self, review_stub):
        """指し示せる箇所が無いので原文をハイライトしない（空スパン）。

        ⚠️ セグメントスコープと同じ「見つからなければ全体をハイライト」にすると
        **文書全体が塗られる**。フロントの `resolveOverlaps` は `end > start` で
        絞るため、空スパンは自然に無視される。
        """
        review_stub.detect = _honest_detect
        result = run_review_agent_core(GOOD_LP.rsplit("\n", 1)[0])

        for finding in self._doc_findings(result):
            assert (finding.start, finding.end) == (0, 0), (
                f"文書全体（{finding.start}-{finding.end}）をハイライトしている"
            )
            assert finding.excerpt == ""

    def test_document_segment_is_not_exposed_as_a_segment(self, review_stub):
        """UI のセグメント一覧には擬似セグメントを混ぜない。"""
        result = run_review_agent_core(GOOD_LP)

        assert DOCUMENT_SEGMENT_ID not in {s.segment_id for s in result.segments}

    def test_excerpt_is_resolved_when_the_llm_can_point_at_text(self, review_stub):
        """指し示せる箇所があれば原文オフセットへ解決する（規程不一致など）。"""
        review_stub.detect = lambda _t, rule, _e: DetectVerdict(
            violates=True, message="規程と異なる", suggestion="修正",
            excerpt="商品到着後8日以内",
        ) if rule.rule_id in _always_check_ids() else DetectVerdict(violates=False)

        result = run_review_agent_core(GOOD_LP)

        doc_findings = self._doc_findings(result)
        assert doc_findings
        for finding in doc_findings:
            assert finding.excerpt == "商品到着後8日以内"
            assert GOOD_LP[finding.start:finding.end] == finding.excerpt


# =============================================================================
# ④ 規程の検索クエリ
# =============================================================================

class TestEvidenceQuery:

    def test_document_rules_query_by_the_rule_itself(self, review_stub):
        """検索クエリはルール自身（文書全体をクエリにしない）。

        文書をそのままクエリにすると、長文では埋め込みが薄まって関連する規程を
        引けない。探したいのは「このルールの根拠条文」である。
        """
        run_review_agent_core(GOOD_LP)

        queries = [kwargs.get("query") for name, kwargs in review_stub.tool_calls
                   if name == "rag_search"]
        assert queries
        assert GOOD_LP not in queries, "文書全体を検索クエリにしている"

        rs = get_ruleset("ec_ad")
        for rule in rs.always_check_rules:
            if rule.evidence_query:
                # ⚠️ `evidence_query` を持つルールは**意図的に**ルール本文で
                # 検索しない。条文コレクションにはルール自身が 1 行として入って
                # いるため、ルール本文で引くと自分自身を引き当ててしまう
                # （policy-01 が引きたいのは自社の実際の規程）。詳細は
                # `RuleItem.evidence_query` の宣言箇所。
                continue
            assert any(rule.title in q for q in queries), (
                f"{rule.rule_id} の根拠をルール本文で検索していない"
            )

    def test_rules_with_an_override_use_it(self, review_stub):
        """`evidence_query` を持つルールはその文字列で検索すること。"""
        run_review_agent_core(GOOD_LP)

        queries = [kwargs.get("query") for name, kwargs in review_stub.tool_calls
                   if name == "rag_search"]
        rs = get_ruleset("ec_ad")
        overridden = [r for r in rs.always_check_rules if r.evidence_query]
        assert overridden, "上書きを持つルールが 1 件も無い（前提が崩れている）"
        for rule in overridden:
            assert rule.evidence_query in queries, (
                f"{rule.rule_id}: evidence_query が使われていない"
            )
            assert not any(rule.title in q for q in queries), (
                f"{rule.rule_id}: ルール本文でも検索している（自己一致する）"
            )

    def test_rules_with_a_collection_override_narrow_the_scope(self, review_stub):
        """`evidence_collections` を持つルールはその範囲だけを検索すること。

        ⚠️ **範囲を絞らないと自己一致が消えない。** クエリだけ変えても
        条文コレクションが候補に残っていると、そこから何かが引かれる。
        """
        run_review_agent_core(GOOD_LP)

        rs = get_ruleset("ec_ad")
        by_query = {
            kwargs.get("query"): kwargs.get("allowed_collections")
            for name, kwargs in review_stub.tool_calls if name == "rag_search"
        }
        for rule in rs.always_check_rules:
            if not rule.evidence_collections:
                continue
            assert by_query[rule.evidence_query] == rule.evidence_collections, (
                f"{rule.rule_id}: 検索範囲が絞られていない"
            )
