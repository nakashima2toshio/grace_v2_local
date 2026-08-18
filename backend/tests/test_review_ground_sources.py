# backend/tests/test_review_ground_sources.py
"""**④ Ground へ検査対象の本文も渡す**ことを固定するテスト。

## 背景（実測 2026-08-19 05:35 / 条文コレクション登録 + #93 の後）

指摘の件数も内容も正しくなった（送料 / 支払方法 / 引渡時期の 3 件）。にもかかわらず、
3 件すべてが **確信度 1.00 / confirmed** で出ていた。その 1.00 が何も測っていない。

`_evaluate` は ④ Ground に**規程（条文）だけ**を根拠として渡していた。

    gres = verifier.verify(..., finding.message, evidence_texts)

ところが表記漏れルールの指摘文は「〜の記載がない」という**対象文書についての
主張**である。条文は対象文書について何も述べていないので、原理的に検証できない。

実測の判定内訳がそれをそのまま示している。

    tokusho-03 —
      supported: 特定商取引法第11条は商品の引渡時期の記載を求めている   ← 条文の言い直し
      supported: 注文からどの程度で商品が届くかが読み取れない広告は違反 ← 条文の言い直し
      neutral  : 当該記述には商品の引渡時期の記載が見当たらない         ← 本題
      neutral  : 当該記述は特定商取引法第11条に抵触する                 ← 本題

支持された 2 件は**条文が条文自身を支持している**だけで、本題の 2 件は条文からは
判定できないので neutral。`support_rate = 2/(2+0) = 1.00` → confirmed。
#89 で入れた `judged = verified and decided > 0` も、トートロジーが decided を
埋めてしまうので機能しない。

逆向きの誤りも同じ実行で出ていた。tokusho-01 では
「送料が別途必要かどうかの記載がない」が **supported** と判定されている。条文は
当該広告について何も述べていない（条文が「送料の記載が無い場合」に言及している
だけ）。**偽の supported** である。

## 直し方

指摘文には 2 種類の主張が混ざっている。

| 主張 | 例 | 検証できる出典 |
|---|---|---|
| (i) 対象文書についての事実 | 「送料の記載がない」 | **対象本文** |
| (ii) 法的な結論 | 「第11条に抵触する」 | 条文 |

両方を根拠として渡せば、各主張が正しい出典に照合される。

ここで固定すること:
  1. ④ Ground の sources に**対象本文**が入ること
  2. 規程（条文）も従来どおり入ること（(ii) の検証を失わない）
  3. 文書全体パスでもセグメントパスでも入ること
  4. 規程が 0 件（条文フォールバック）でも入ること
  5. **③ Detect には足さない**こと（対象テキストは別枠で渡している）

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from backend.app.core.review_agent import DOCUMENT_SEGMENT_ID, run_review_agent_core
from backend.app.core.review_gates import DetectVerdict

# 実測に合わせた「表記漏れがある広告文」。
DOCUMENT = (
    "当社の美容液は、うるおいを与えて肌をなめらかに整えます。"
    "■ 特定商取引法に基づく表記 販売業者: 株式会社サンプル "
    "販売価格: 4,980円（税込）"
)


def _sources_of(stub) -> list[list[str]]:
    return [sources for _query, _message, sources in stub.verify_calls]


# =============================================================================
# ① 対象本文が根拠として渡る
# =============================================================================

class TestTargetTextIsGrounded:

    def test_document_text_reaches_the_verifier(self, review_stub):
        """④ Ground の sources に対象本文が含まれること。

        ⚠️ **これが無いと「〜の記載がない」を検証する材料がゼロになる。**
        """
        run_review_agent_core(DOCUMENT)

        assert review_stub.verify_calls, "④ Ground が 1 度も呼ばれていない"
        for sources in _sources_of(review_stub):
            assert any(DOCUMENT in s for s in sources), (
                f"対象本文が根拠に入っていない: {sources}"
            )

    def test_evidence_is_still_included(self, review_stub):
        """規程（条文）も従来どおり渡ること（法的結論の検証を失わない）。"""
        run_review_agent_core(DOCUMENT)

        for sources in _sources_of(review_stub):
            assert any("優良" in s for s in sources), (
                f"規程が根拠から消えている: {sources}"
            )
        # 対象本文の分だけ増えている
        for sources in _sources_of(review_stub):
            assert len(sources) >= 2

    def test_target_text_is_labelled(self, review_stub):
        """対象本文はラベル付きで渡すこと。

        ラベルが無いと、検証 LLM が「規程にそう書いてある」と誤読して
        (i) の主張まで条文由来として扱いうる。
        """
        run_review_agent_core(DOCUMENT)

        for sources in _sources_of(review_stub):
            labelled = [s for s in sources if DOCUMENT in s]
            assert labelled
            assert all("検査対象" in s for s in labelled), (
                f"対象本文にラベルが無い: {labelled}"
            )


# =============================================================================
# ② 両方の判定経路で渡る
# =============================================================================

class TestBothPasses:

    def test_document_scope_pass_includes_the_whole_document(self, review_stub):
        """文書全体パス（always_check）でも渡ること。

        ⚠️ 既定の stub detect はキーワード駆動なので、キーワードを持たない
        `always_check` ルールは発火しない。ここは表記漏れの検証が主題なので、
        detect を「必ず違反」に差し替えて文書全体パスを通す。
        """
        review_stub.detect = lambda _text, rule, _evidence: DetectVerdict(
            violates=True,
            message=f"{rule.title}の記載が見当たりません",
            suggestion="追記してください",
            excerpt="",
        )

        result = run_review_agent_core(DOCUMENT)

        doc_findings = [
            f for f in result.findings if f.segment_id == DOCUMENT_SEGMENT_ID
        ]
        assert doc_findings, "文書全体パスの指摘が 1 件も出ていない"
        assert any(
            any(DOCUMENT in s and "検査対象" in s for s in sources)
            for sources in _sources_of(review_stub)
        ), "文書全体パスで対象本文が根拠に入っていない"

    def test_segment_pass_includes_the_segment(self, review_stub):
        """セグメントパスでは**そのセグメント**が渡ること（文書全体ではなく）。

        判定単位と検証材料がずれると、別の段落の記載を根拠に
        「記載がある／ない」を判定してしまう。
        """
        result = run_review_agent_core(DOCUMENT)

        seg_findings = [
            f for f in result.findings if f.segment_id != DOCUMENT_SEGMENT_ID
        ]
        if not seg_findings:
            return  # キーワード一致が無いシナリオでは対象外

        excerpt = seg_findings[0].excerpt
        assert any(
            any(excerpt in s and "検査対象" in s for s in sources)
            for sources in _sources_of(review_stub)
        ), "セグメント本文が根拠に入っていない"

    def test_included_even_when_no_evidence_was_retrieved(self, review_stub):
        """規程 0 件（条文フォールバック）でも対象本文は渡ること。"""
        review_stub.rag_output = None

        run_review_agent_core(DOCUMENT)

        assert review_stub.verify_calls
        for sources in _sources_of(review_stub):
            assert any(DOCUMENT in s for s in sources)


# =============================================================================
# ③ ③ Detect には足さない
# =============================================================================

class TestDetectIsUnchanged:
    """⚠️ ③ Detect のプロンプトは【規程】と【対象テキスト】を別枠で持つ。

    `evidence` に対象本文を混ぜると「規程にそう書いてある」と読まれ、
    #88 / #90 で入れた主題スコープの制約が崩れる。
    """

    def test_detect_evidence_has_no_target_text(self, review_stub):
        run_review_agent_core(DOCUMENT)

        assert review_stub.detect_calls
        for _text, rule_id, evidence in review_stub.detect_calls:
            assert "検査対象" not in evidence, (
                f"{rule_id}: ③ Detect の規程に対象本文が混ざっている"
            )
