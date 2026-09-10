# tests/grace/test_vertical_scope.py
"""業界プロファイルのコア配線（検索スコープ・方針注入）の単体テスト。

Qdrant・API キー不要。
対象:
- RAGSearchTool._apply_allowed_collections（許可リスト型の検索スコープ制限）
- ReasoningTool._build_prompt（config.llm.prompt_addendum の注入）
- 設定既定値（allowed_collections / prompt_addendum）
- agent_support_example.PROFILES の実コレクション名（命名規約 `*_anthropic`）
"""
from agent_support_example import PROFILES
from grace.config import GraceConfig, LLMConfig, QdrantConfig
from grace.tools import RAGSearchTool, ReasoningTool


def make_reasoning_tool(config: GraceConfig) -> ReasoningTool:
    """LLM クライアントを生成せずに ReasoningTool を組み立てる（_build_prompt 検証用）。"""
    tool = ReasoningTool.__new__(ReasoningTool)
    tool.config = config
    tool.model_name = config.llm.model
    return tool


class TestApplyAllowedCollections:
    CANDIDATES = ["wikipedia_ja", "livedoor", "cc_news", "gov_faq_anthropic"]
    # 実環境はサフィックス付きコレクション名が多い（ライブ実行ログで確認された実名）
    REAL_CANDIDATES = ["cc_news_2per_anthropic", "wikipedia_ja_5per", "cc_news_2per",
                       "fineweb_edu_ja_5per"]

    def test_empty_allowlist_means_no_restriction(self):
        assert RAGSearchTool._apply_allowed_collections(self.CANDIDATES, []) == self.CANDIDATES

    def test_scopes_to_intersection_preserving_order(self):
        allowed = ["gov_faq_anthropic", "wikipedia_ja"]
        assert RAGSearchTool._apply_allowed_collections(self.CANDIDATES, allowed) == [
            "wikipedia_ja", "gov_faq_anthropic",
        ]

    def test_blocks_out_of_scope_fallback(self):
        # 業界外コレクション（livedoor/cc_news）へのフォールバック漏れを塞ぐ
        scoped = RAGSearchTool._apply_allowed_collections(self.CANDIDATES, ["wikipedia_ja"])
        assert scoped == ["wikipedia_ja"]

    def test_partial_match_scopes_suffixed_collection_names(self):
        # 完全一致だと wikipedia_ja_5per に一致せずスコープが素通りするバグの回帰テスト。
        # search_priority と同じ部分一致（含有）で判定する
        allowed = ["gov_faq_anthropic", "gov_laws_anthropic", "wikipedia_ja"]
        scoped = RAGSearchTool._apply_allowed_collections(self.REAL_CANDIDATES, allowed)
        assert scoped == ["wikipedia_ja_5per"]

    def test_no_match_falls_back_to_unrestricted(self):
        # 専用コレクション未登録の段階ではデモが動くよう制限を適用しない（警告のみ）
        allowed = ["saas_docs_anthropic", "saas_api_anthropic"]
        assert RAGSearchTool._apply_allowed_collections(self.CANDIDATES, allowed) == self.CANDIDATES
        assert RAGSearchTool._apply_allowed_collections(self.REAL_CANDIDATES, allowed) == self.REAL_CANDIDATES

    def test_empty_candidates_stay_empty(self):
        assert RAGSearchTool._apply_allowed_collections([], ["gov_faq_anthropic"]) == []


class TestPromptAddendumInjection:
    def test_addendum_is_injected_into_prompt(self):
        config = GraceConfig()
        config.llm.prompt_addendum = "断定を避け、該当ページ・担当課を明示。個人情報は尋ねない。"
        tool = make_reasoning_tool(config)
        prompt = tool._build_prompt("住民票の取り方は？", context=None, sources=None)
        assert "【業務方針（遵守）】" in prompt
        assert "断定を避け" in prompt
        # 方針はシステム指示の直後（参照情報・質問より前）に入る
        assert prompt.index("【業務方針（遵守）】") < prompt.index("【ユーザーの質問】")

    def test_no_addendum_no_section(self):
        tool = make_reasoning_tool(GraceConfig())
        prompt = tool._build_prompt("住民票の取り方は？", context=None, sources=None)
        assert "【業務方針（遵守）】" not in prompt

    def test_addendum_coexists_with_context_and_sources(self):
        config = GraceConfig()
        config.llm.prompt_addendum = "製品バージョンを明示する。"
        tool = make_reasoning_tool(config)
        sources = [{"score": 0.9, "collection": "saas_docs_anthropic",
                    "payload": {"question": "Q", "answer": "A", "source": "doc.md"}}]
        prompt = tool._build_prompt("APIのレート制限は？", context="補足", sources=sources)
        assert "【業務方針（遵守）】" in prompt
        assert "【参照情報】" in prompt
        assert "【補足コンテキスト】" in prompt


class TestConfigDefaults:
    def test_allowed_collections_default_empty(self):
        assert QdrantConfig().allowed_collections == []

    def test_prompt_addendum_default_empty(self):
        assert LLMConfig().prompt_addendum == ""


class TestProfileCollections:
    """PROFILES.collections が実コレクション名（命名規約準拠）であること。"""

    KNOWN_DEFAULTS = {"wikipedia_ja", "livedoor", "cc_news", "japanese_text"}

    def test_collection_names_follow_convention(self):
        for key, profile in PROFILES.items():
            assert profile.collections, f"{key}: collections が空"
            for name in profile.collections:
                assert name.endswith("_anthropic") or name in self.KNOWN_DEFAULTS, (
                    f"{key}: 実在し得ないコレクション名 {name!r}"
                    "（命名規約 *_anthropic か既定コレクションを使う）"
                )

    def test_vertical_prefix_matches_profile(self):
        for key, profile in PROFILES.items():
            dedicated = [c for c in profile.collections if c.endswith("_anthropic")]
            assert dedicated, f"{key}: 専用コレクションが 1 つもない"
            for name in dedicated:
                assert name.startswith(f"{key}_"), (
                    f"{key}: 専用コレクション {name!r} は '{key}_' で始まる命名にする"
                )
