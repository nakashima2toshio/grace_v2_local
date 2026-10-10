# tests/test_review_document_context.py
"""段落単位の ③ Detect に文書の文脈を渡すこと、tokusho-01 の税込表記の判定基準。

## 回帰（実測 2026-10-02・化粧品LP案・grace_v2_local のローカル LLM）

1. 大きい方のローカル LLM が yakki-01（**食品**の医薬品的効能標榜）を
   「シミが治る」で指摘した。判定基準には「化粧品なら violates=false」とあるが、
   判定に渡るのは段落「使い続ければシミが治ると評判で…」だけで、商品名「美容液」は
   1 段落目にしかない → 化粧品だと分かる材料が無かった。
2. ローカル LLM 2 種とも tokusho-01（販売価格の税込表示）を取りこぼした。「4,980円」が
   書いてあることで価格表示ありと判断した（共通の指示「記載の有無だけを見る」）。

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.core.review_agent import (
    DOCUMENT_CONTEXT_CHARS,
    _document_context,
    run_review_agent_core,
)
from backend.app.core.review_gates import create_violation_detector
from backend.app.core.rulesets import EC_AD

# frontend/src/components/ReviewForm.tsx の「NG 例（優良誤認・薬機法）」と同じ本文
COSMETIC_LP = (
    "当社の美容液は業界No.1の実力です。\n"
    "\n"
    "使い続ければシミが治ると評判で、副作用がないので誰でも安心してお使いいただけます。\n"
    "\n"
    "・今だけ期間限定 通常価格 12,000円 → 4,980円\n"
    "・送料無料"
)


class TestSegmentJudgeGetsDocumentContext:

    def test_yakki01_sees_that_the_product_is_a_cosmetic(self, review_stub):
        """「シミが治る」の段落を判定するとき、文脈に「美容液」が入っていること。"""
        run_review_agent_core(COSMETIC_LP, document_title="化粧品LP案")

        calls = [(text, ctx) for rid, text, ctx in review_stub.detect_contexts
                 if rid == "yakki-01"]
        assert calls, "yakki-01 が判定に回っていない"
        for text, ctx in calls:
            assert "美容液" not in text, "前提: 段落そのものには商品名が無い"
            assert "美容液" in ctx
            assert "題名: 化粧品LP案" in ctx

    def test_document_scope_rules_get_no_context(self, review_stub):
        """文書全体で判定するルールは本文が文書全体なので、文脈を重ねない。"""
        run_review_agent_core(COSMETIC_LP)

        doc = [ctx for rid, _t, ctx in review_stub.detect_contexts if rid == "tokusho-01"]
        assert doc and all(ctx == "" for ctx in doc)


class TestDocumentContext:

    def test_untitled_is_omitted(self):
        assert _document_context("本文", "無題") == "本文"
        assert _document_context("本文", "") == "本文"

    def test_long_document_is_cut(self):
        ctx = _document_context("あ" * (DOCUMENT_CONTEXT_CHARS + 50))
        assert ctx.endswith("…（以下略）")
        assert ctx.count("あ") == DOCUMENT_CONTEXT_CHARS


@pytest.fixture
def prompt(monkeypatch):
    """`detect()` が LLM へ渡したプロンプト文字列を返す。"""
    captured: list[str] = []

    def _generate(**kwargs):
        captured.append(kwargs["contents"])
        return SimpleNamespace(text='{"violates": false}')

    client = SimpleNamespace(models=SimpleNamespace(generate_content=_generate))
    module = SimpleNamespace(create_chat_client=lambda _c: client)
    monkeypatch.setitem(sys.modules, "grace", SimpleNamespace(llm_compat=module))
    monkeypatch.setitem(sys.modules, "grace.llm_compat", module)

    def _build(rule_id="yakki-01", text="使い続ければシミが治ると評判です。", context=""):
        detect = create_violation_detector(
            SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))
        )
        detect(text, EC_AD.rule_by_id(rule_id), "規程本文", context=context)
        return captured[-1]

    return _build


class TestPrompt:

    def test_context_block_is_added_before_the_target(self, prompt):
        text = prompt(context="当社の美容液は業界No.1の実力です。")
        assert "# 文書の文脈（参考）" in text
        assert "ここにだけ書かれている内容を指摘しないこと" in text
        assert text.index("当社の美容液") < text.index("# 対象テキスト")

    def test_no_context_no_block(self, prompt):
        assert "# 文書の文脈" not in prompt()

    def test_yakki01_criteria_point_at_the_context(self, prompt):
        assert "対象テキスト（または【文書の文脈】）の商品が化粧品" in prompt()

    def test_tokusho01_checks_the_tax_notation(self, prompt):
        text = prompt("tokusho-01", "販売価格: 4,980円")
        assert "価格の数字があるだけでは足りない" in text
        assert "税込か税別かを示す表記" in text

    def test_tokusho01_public_description_is_unchanged(self):
        """指示文は 2 段落目に足した。登録済みの規程行・検索クエリは変わらない。"""
        assert EC_AD.rule_by_id("tokusho-01").public_description().endswith(
            "税込／税別が不明瞭な場合は表示義務違反となる。")
