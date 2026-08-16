# backend/tests/test_source_attribution.py
"""reasoning プロンプトが **出典の種別を偽らせない** ことを固定するテスト。

## 背景（実測）

質問「明日の東京の天気は？」への回答:

    Yahoo!天気によると、…確認できる情報源があります（**社内ナレッジ（web_search）**）。

Yahoo!天気は社内ナレッジではない。原因は回答規則が出典の種別を問わず

    「社内ナレッジ（出典ファイル名）によると...」の形式で出典を明示してください

と指示していたこと。LLM は指示どおりに従っただけである。

## なぜ最優先で固定するのか

**どのゲートでも検出できない。** 述べている内容自体は情報源に忠実なので
groundedness は下がらない（実測 1.00）。回答も空でなく、出典もある。
根拠の信頼性を売りにするシステムで **外部 Web を社内の裏付けとして提示する**
のは、静かに起きるぶん最も危険な壊れ方である。

⚠️ LLM にも Qdrant にも接続しない（`_build_prompt` は純粋な文字列組み立て）。
"""
from __future__ import annotations

import pytest

from grace.config import GraceConfig
from grace.tools import ReasoningTool

# 実測の情報源（cc_news の社内 Q&A と、SerpAPI の Web 結果）
INTERNAL_SOURCE = {
    "score": 0.6658,
    "payload": {
        "domain": "cc_news_2per_anthropic",
        "question": "バトンルージュの天気予報はどうなっていますか？",
        "answer": "月曜日の夜間ラッシュ時は雷と雨がありました。",
        "source": "qa_pairs_cc_news_2per_chunks.csv",
    },
}
WEB_SOURCE = {
    "score": 0.9,
    "collection": "web_search",
    "payload": {
        "question": "",
        "answer": "東京（東京）の天気予報。今日・明日の天気と風と波…",
        "source": "https://weather.yahoo.co.jp/weather/jp/13/4410.html",
    },
}


# =============================================================================
# ① 種別の判定
# =============================================================================

class TestSourceOrigin:

    def test_web_search_collection_is_web(self):
        assert ReasoningTool._source_origin(WEB_SOURCE) == "Web"

    def test_internal_collection_is_internal(self):
        assert ReasoningTool._source_origin(INTERNAL_SOURCE) == "社内"

    def test_url_source_is_web_even_without_collection(self):
        """collection が落ちた経路でも URL なら Web と判定すること。

        `_collect_citations`（画面の出典ラベル）と同じ規則にそろえる。
        """
        source = {"payload": {"source": "https://example.com/a"}}
        assert ReasoningTool._source_origin(source) == "Web"

    @pytest.mark.parametrize("url", ["http://example.com", "https://example.com"])
    def test_both_schemes_are_web(self, url):
        assert ReasoningTool._source_origin({"payload": {"source": url}}) == "Web"

    def test_csv_filename_is_internal(self):
        source = {"payload": {"source": "qa_pairs_gov_faq_chunks.csv"}}
        assert ReasoningTool._source_origin(source) == "社内"

    def test_unknown_source_defaults_to_internal(self):
        """判別できないものは社内扱い。

        ⚠️ 危険側は「社内のものを Web と書く」ではなく「**Web のものを社内と
        書く**」なので、既定を社内に倒すこと自体は安全側ではない。ここは
        「Web と判る印が無ければ社内」という現行の割り切りを明示的に固定する
        （出典が URL でも web_search でもないなら社内 CSV しかありえない）。
        """
        assert ReasoningTool._source_origin({"payload": {}}) == "社内"


# =============================================================================
# ② プロンプトに種別が載る
# =============================================================================

class TestPromptLabelsEachSource:

    def test_web_source_is_labelled_web(self):
        prompt = _build_prompt(sources=[WEB_SOURCE])
        assert "【Web】" in prompt

    def test_internal_source_is_labelled_internal(self):
        prompt = _build_prompt(sources=[INTERNAL_SOURCE])
        assert "【社内】" in prompt

    def test_mixed_sources_get_distinct_labels(self):
        """実測と同じ「社内 5 件 + Web 9 件」の混在を想定する。"""
        prompt = _build_prompt(sources=[INTERNAL_SOURCE, WEB_SOURCE])

        internal_line = _source_header(prompt, 1)
        web_line = _source_header(prompt, 2)

        assert "【社内】" in internal_line and "【Web】" not in internal_line
        assert "【Web】" in web_line and "【社内】" not in web_line

    def test_label_is_on_the_source_header(self):
        """本文ではなく見出しに付けること（LLM が対応付けを間違えないように）。"""
        header = _source_header(_build_prompt(sources=[WEB_SOURCE]), 1)
        assert header.startswith("--- 情報源 1 【Web】")

    def test_existing_header_fields_are_kept(self):
        header = _source_header(_build_prompt(sources=[WEB_SOURCE]), 1)
        assert "信頼度: 0.90" in header
        assert "コレクション: web_search" in header


# =============================================================================
# ③ 回答規則が種別で分岐する
# =============================================================================

class TestAttributionRule:

    def test_rule_covers_both_origins(self):
        rules = _build_prompt(sources=[WEB_SOURCE])

        assert "社内ナレッジ（出典ファイル名）" in rules, "社内側の書式が消えている"
        assert "Web 検索結果" in rules, "Web 側の書式が無い"

    def test_rule_forbids_calling_web_internal(self):
        """「Web を社内ナレッジと書くな」を明示すること。

        書式を 2 つ並べるだけでは、実測のようにモデルが片方へ寄せてしまう。
        """
        rules = _build_prompt(sources=[WEB_SOURCE])
        assert "「社内ナレッジ」と書いてはいけません" in rules

    def test_rule_is_not_unconditionally_internal(self):
        """出典種別を無視して社内ナレッジを強制する旧文言が残っていないこと。

        ⚠️ この 1 行が実測の誤帰属の原因だった。
        """
        rules = _build_prompt(sources=[WEB_SOURCE])
        assert "回答の根拠となった情報がある場合、「社内ナレッジ" not in rules

    def test_rule_is_present_even_without_sources(self):
        assert "Web 検索結果" in _build_prompt(sources=[])


# =============================================================================
# ④ Web 情報源どうしの取り違えを防ぐ
# =============================================================================

class TestCrossSourceAttributionRule:
    """種別（社内/Web）が正しくなった**後に**残った誤りへの対策。

    実測（除外を入れた後の「明日の東京の天気は？」）:

    1. 取り違え — 情報源 7（Yahoo!天気）の文章に、情報源 8（tenki.jp）の
       URL を付けた:

           Web 検索結果（tenki.jp/forecast/3/16/）によると:
             「明日」までの天気予報、風、波、明日までの6時間ごとの降水確率、
             最高・最低気温を確認できます

       この文言は Yahoo のスニペットのものである。種別（Web）は合っているが
       **どの Web 情報源かが違う。**

    2. ドメインの捏造 — `webath.co.jp` と書いた（正しくは `weathernews.jp`）。

    どちらも規則が「サイト名または URL」と書いていて、**記憶から補う余地**を
    残していたことが原因。URL の丸写しを求めることで、対応付けを記憶ではなく
    転記の作業にする。
    """

    def test_requires_copying_the_source_line_verbatim(self):
        rules = _build_prompt(sources=[WEB_SOURCE])
        assert "そのまま省略せずに" in rules, "URL の丸写しを求めていない"

    def test_forbids_supplying_domains_from_memory(self):
        """ドメイン捏造（`webath.co.jp`）への直接の歯止め。"""
        rules = _build_prompt(sources=[WEB_SOURCE])

        assert "記憶から補わないでください" in rules
        assert "捏造にあたります" in rules

    def test_does_not_offer_site_name_as_an_alternative_to_url(self):
        """「サイト名または URL」の逃げ道を残していないこと。

        ⚠️ この選択肢が取り違えとドメイン捏造の両方を許していた。
        """
        rules = _build_prompt(sources=[WEB_SOURCE])
        assert "サイト名または URL" not in rules

    def test_requires_one_source_per_statement(self):
        """1 つの記述に情報源を 1 つだけ対応させること。

        混ぜると、どの記述がどの出典に対応するのか読者にも検証できなくなる。
        """
        rules = _build_prompt(sources=[WEB_SOURCE])

        assert "1 つだけ対応させ" in rules
        assert "1 つの箇条書きに混ぜないでください" in rules


# =============================================================================
# ⑤ 内部の通し番号を回答に出さない
# =============================================================================

class TestInternalNumberingIsNotExposed:
    """実測: 「別の情報源（**情報源7**）で…」と回答本文に書いた。

    「情報源 N」はこのプロンプト内部の通し番号で、回答を読む人には何のことか
    分からない。番号自体はモデルが情報源を区別するのに有用なので**残す**が、
    出力してはいけない、と明示する。
    """

    def test_rule_forbids_referencing_source_numbers(self):
        rules = _build_prompt(sources=[WEB_SOURCE])

        assert "情報源番号を書かない" in rules
        assert "内部の通し番号" in rules

    def test_tells_what_to_write_instead(self):
        """禁止だけでは代替が分からない。書くべきものを示すこと。"""
        rules = _build_prompt(sources=[WEB_SOURCE])
        assert "代わりに出典の URL やファイル名を書きます" in rules

    def test_headers_still_carry_the_numbering(self):
        """番号自体はプロンプトに残っていること（モデルの区別用）。"""
        prompt = _build_prompt(sources=[INTERNAL_SOURCE, WEB_SOURCE])

        assert "--- 情報源 1 " in prompt
        assert "--- 情報源 2 " in prompt


# =============================================================================
# ⑥ 規則の通し番号が壊れていない
# =============================================================================

class TestRulesAreWellFormed:

    def test_numbering_is_sequential(self):
        """規則を足したときに番号が飛んだり重複したりしていないこと。"""
        rules = _build_prompt(sources=[WEB_SOURCE])
        block = rules.split("【回答の構成ルール（最重要）】", 1)[1]

        numbers = [
            int(line.split(".", 1)[0])
            for line in block.splitlines()
            if line[:1].isdigit() and ". **" in line
        ]
        assert numbers == list(range(1, len(numbers) + 1)), f"番号が不正: {numbers}"

    def test_original_rules_survived(self):
        """既存の規則（誠実さ・事実優先・丁寧さ・捏造禁止）が消えていないこと。"""
        rules = _build_prompt(sources=[WEB_SOURCE])

        for keyword in ("正確性と誠実さ", "判明した事実を優先", "丁寧な日本語", "捏造禁止"):
            assert keyword in rules, f"{keyword} が消えている"


# =============================================================================
# helpers
# =============================================================================

def _build_prompt(*, sources=None, context=None) -> str:
    tool = ReasoningTool.__new__(ReasoningTool)
    tool.config = GraceConfig()
    return tool._build_prompt("明日の東京の天気は？", context, sources)


def _source_header(prompt: str, index: int) -> str:
    for line in prompt.splitlines():
        if line.startswith(f"--- 情報源 {index} "):
            return line
    raise AssertionError(f"情報源 {index} の見出しが無い:\n{prompt}")
