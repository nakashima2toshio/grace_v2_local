# agent_service.py
# =============================================================================
# 【責務】
#   - ReAct（Thought → Action → Observation）＋ Reflection（自己評価・推敲）による
#     ハイブリッド・ナレッジ・エージェント（ReActAgent クラス）の実装。
#   - Anthropic Claude の Tool Use を用いた RAG 検索ツール
#     （search_rag_knowledge_base / list_rag_collections）の呼び出し制御と、
#     ツール結果を含む会話履歴（self._messages）の自前管理。
#   - システムプロンプト・ツール定義（input_schema）の構築、
#     キーワード抽出（MeCab/regex）によるクエリ拡張、最終回答の整形。
#   - 検索失敗時の未回答質問ログ記録（log_unanswered_question）と
#     Qdrant コレクション一覧取得ヘルパーの提供。
#
# 【IPO（簡略）】
#   Input  : ユーザー質問（user_input）、選択コレクション一覧、モデル名、
#            セッションID、ハイブリッド検索フラグ、設定値（get_config）
#   Process: キーワード抽出でクエリ拡張
#            → ReAct ループ（generate_with_tools で Tool Use 判定 → RAG 検索実行
#               → tool_result を会話履歴へ追記、最大 agent.max_turns 回）
#            → Reflection フェーズで回答案を自己評価・修正
#            → "Answer:" / "Final Answer:" を抽出して最終回答を整形
#   Output : 進捗イベントのジェネレータ
#            （type: log / tool_call / tool_result / final_text / final_answer）
#            と最終回答テキスト。副作用として思考ログ（thought_log）蓄積・
#            未回答質問ログ出力。
# =============================================================================

import re
import uuid
from typing import Any, Dict, Generator, List, Optional

# noqa: F401  レガシーテストの patch ターゲットとして残置
from qdrant_client import (
    QdrantClient,  # noqa: F401
)

from agent_tools import (
    RAGToolError,
    list_rag_collections,
    search_rag_knowledge_base,
    search_rag_knowledge_base_cached,
)

# [MIGRATION] from google import genai / from google.genai import types を削除
# [MIGRATION] AnthropicClient を helper_llm 経由で使用
from helper.helper_llm import ToolUseResponse, create_llm_client
from qdrant_client_wrapper import get_qdrant_client

# 設定サービスからロガーと設定を取得
from services.config_service import get_config, logger
from services.log_service import log_unanswered_question

# キーワード抽出（オプション）
try:
    from regex_mecab import KeywordExtractor

    KEYWORD_EXTRACTION_AVAILABLE = True
except ImportError:
    KEYWORD_EXTRACTION_AVAILABLE = False
    KeywordExtractor = None

# キャッシュと並列検索をインポート

# 孤立サロゲート（U+D800–U+DFFF）を除去する。KeywordExtractor が不正な
# Unicode を返すと、そのまま Anthropic API へ送った際に JSON エンコードで
# UnicodeEncodeError（surrogates not allowed）が発生するため、事前に除去する。
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _strip_surrogates(text: str) -> str:
    """文字列から孤立サロゲートを取り除く（UTF-8 にエンコード不能な文字対策）。"""
    if not isinstance(text, str):
        return text
    return _SURROGATE_RE.sub("", text)


# -----------------------------------------------------------------------------
# Constants & Configuration
# -----------------------------------------------------------------------------

SYSTEM_INSTRUCTION_TEMPLATE = """
あなたは、社内ドキュメント検索システムと連携した「ハイブリッド・ナレッジ・エージェント」です。
あなたの役割は、ユーザーの質問に対して、一般的な知識と、提供されたツール（社内ナレッジ検索）を適切に使い分けて回答することです。

## ReAct プロセスと出力フォーマット (厳守)

あなたは **Thought (思考)**、**Action (ツール実行)**、**Observation (結果観察)** のサイクルを回して回答に到達する必要があります。

### 1. ツールを使用する場合（検索が必要な場合）
必ず以下の形式で思考を出力してから、ツールを呼び出してください。
**Thought: [なぜ検索が必要か、どんなクエリで検索するか]**
(この後にツール呼び出しが行われます)
**重要:
- 検索クエリを作成する際は、提供された「重要キーワード」を必ず含めてください。
- `collection_name` パラメータは絶対に指定しないでください。システムが自動的に全コレクションから最適なものを選択します。**

### 2. 最終回答を行う場合（検索が完了した、または検索不要な場合）
必ず以下の形式で出力してください。
**Thought: [得られた情報に基づいてどう回答するか、または検索結果がなかった場合の判断]**
**Answer: [ユーザーへの最終的な回答]**

**重要:**
- 検索クエリは、質問文から「いつ」「誰」「何」などの具体的な要素を抽出して作成してください。抽象的な質問（例：「教えて」）をそのまま検索クエリにせず、具体的なキーワードに変換してください。
- 検索結果のスコアが低くても（例: 0.5程度）、内容が質問に関連していれば、その情報を積極的に使用して回答を作成してください。「情報が見つかりませんでした」と即断せず、得られた断片的な情報からでも回答を試みてください。
- 回答は必ず `Answer:` (または `**Answer:**`) で始めてください。

---

## 行動指針 (Router Guidelines)

1.  **専門知識の検索**:
    *   以下のいずれかに該当する場合は、**必ず `search_rag_knowledge_base` ツールを使用してください。**
        *   プロジェクト固有の仕様、設定、エラー、社内規定、Wikipediaの知識に関する質問。
        *   特定の情報源（例: "Wikipediaによると"、"ライブドアニュースで"）が指定されている質問。
        *   **内容が不明瞭であっても、社内ナレッジに関連する可能性があると判断される質問（例：特定のコード名、システム名、ランダムに見える文字列など）。**
        *   **ただし、一般的なプログラミング言語の文法や使い方に関する質問にはツールを使用しないでください。**
    *   **現在利用可能なコレクションは以下の通りです:**
        {available_collections}

2.  **スマート検索システム（自動コレクション選択）**:
    *   **重要: `search_rag_knowledge_base` ツールを呼び出す際、`collection_name` パラメータは絶対に指定しないでください。**
    *   システムが自動的に以下の戦略で最適なコレクションを選択します：
        *   **キャッシュ優先**: 前回成功したコレクションを優先的に検索
        *   **並列検索**: キャッシュミス時は全コレクションを同時並列検索
        *   **スコアベース選択**: 最もスコアが高い結果を自動的に返す
    *   あなたは `query` パラメータのみを指定してください。例: `search_rag_knowledge_base(query="カリン・フォン・アロルディンゲン")`
    *
    *   **参考: 利用可能なコレクション（自動選択されます）**
        *   `cc_news`: 英語のニュース記事
        *   `wikipedia_ja`: 日本語の百科事典
        *   `livedoor`: 日本語のニュース・ブログ
        *   `japanese_text`: 日本語のWebテキスト
        *   `qa_pairs_custom_upload`, `custom_upload`: ユーザーアップロードの専門Q&A

3.  **一般的な会話**:
    *   挨拶、雑談、単純な計算など、専門知識が不要な場合は、ツールを使わずに `Answer:` で直接回答してください。

4.  **正直さと不足情報の処理 (Critical)**:
    *   ツール検索の結果、情報が得られなかった場合は、**絶対に**あなたの事前学習知識で捏造してはいけません。
    *   「提供された社内ナレッジには関連情報がありませんでした」と正直に伝えてください。

5.  **回答のスタイル**:
    *   丁寧な日本語（です・ます調）で回答してください。
    *   検索結果に基づく回答の場合、「社内ナレッジによると...」や「ソース [ファイル名] によると...」と出典を明示してください。
"""

REFLECTION_INSTRUCTION = """
## Reflection (自己評価と修正)

あなたは上記で作成した「回答案」を、以下の基準で客観的に評価し、必要であれば修正してください。

**チェックリスト:**
1.  **正確性:** 検索結果(もしあれば)に基づいているか？ 提供された情報源に含まれない情報を捏造していないか？
2.  **回答の適切性:** ユーザーの質問に直接的かつ明確に答えているか？
3.  **スタイル:** 親しみやすく、丁寧な日本語（です・ます調）か？ 箇条書きなどを活用して読みやすいか？

**指示:**
*   修正が不要な場合でも、必ず **Final Answer** を出力してください。
*   修正が必要な場合は、修正後の回答を **Final Answer** として出力してください。
*   思考プロセスは `Thought:` で始めてください。

**出力フォーマット:**
Thought: [評価と修正の思考プロセス]
Final Answer: [最終的な回答]
"""

TOOLS_MAP: Dict[str, Any] = {
    'search_rag_knowledge_base': search_rag_knowledge_base,
    'list_rag_collections'     : list_rag_collections
}


# -----------------------------------------------------------------------------
# ReActAgent Class
# -----------------------------------------------------------------------------

class ReActAgent:
    # ★変更: use_hybrid_search パラメータを追加（デフォルトは True）
    def __init__(
        self,
        selected_collections: List[str],
        model_name: str = None,
        session_id: Optional[str] = None,
        use_hybrid_search: bool = True  # ★追加: ハイブリッド検索フラグ
    ):
        self.selected_collections = selected_collections
        # [MIGRATION] モデルデフォルト: "claude-sonnet-4-6"
        self.model_name = model_name or get_config("models.default", "claude-sonnet-4-6")
        self.session_id = session_id or str(uuid.uuid4())
        self.use_hybrid_search = use_hybrid_search

        # [MIGRATION] AnthropicClient (via create_llm_client)
        # チャットセッション管理は messages リストで自前管理するため、
        # _setup_client() / _create_chat() は廃止。
        self.llm = create_llm_client("anthropic", default_model=self.model_name)

        # [MIGRATION] Anthropic はステートレス設計のため、会話履歴を self._messages で管理する。
        # execute_turn() の先頭でリセットされる。
        self._messages: List[Dict[str, Any]] = []

        # システムプロンプトとツール定義を事前構築
        self.system_instruction: str = self._build_system_instruction()
        self.tools: List[Dict[str, Any]] = self._build_tools()

        self.thought_log: List[str] = []

        # キーワード抽出器の初期化
        if KEYWORD_EXTRACTION_AVAILABLE:
            try:
                self.keyword_extractor = KeywordExtractor(prefer_mecab=True)
                logger.info(f"KeywordExtractor initialized successfully. Session: {self.session_id}")
            except Exception as e:
                logger.error(f"Failed to initialize KeywordExtractor: {e}")
                self.keyword_extractor = None
        else:
            self.keyword_extractor = None

        # ★追加: ハイブリッド検索の状態をログ出力
        logger.info(
            f"ReActAgent initialized with session_id: {self.session_id}, "
            f"model: {self.model_name}, use_hybrid_search: {self.use_hybrid_search}"
        )

    # [MIGRATION] _setup_client() / _create_chat() を廃止。
    # APIキー管理は create_llm_client("anthropic") 内部で ANTHROPIC_API_KEY を参照する。

    def _build_system_instruction(self) -> str:
        """システムプロンプトを構築する。

        [MIGRATION] _create_chat() の system_instruction 部分を独立メソッドに分離。
        Anthropic は system= パラメータで渡すため、chat セッションとは切り離す。
        """
        collections_str = (
            ", ".join(self.selected_collections)
            if self.selected_collections
            else "(コレクションが見つかりません)"
        )
        return SYSTEM_INSTRUCTION_TEMPLATE.format(available_collections=collections_str)

    def _build_tools(self) -> List[Dict[str, Any]]:
        """ツール定義を Anthropic Tool Use 形式（input_schema）で構築する。

        [MIGRATION] Gemini 形式 (Python 関数参照) → Anthropic Tool Use 形式 (dict リスト)。
          - "parameters" キー → "input_schema" キー
          - Python 関数参照 → プレーンな dict
        """
        return [
            {
                "name"        : "search_rag_knowledge_base",
                "description" : (
                    "社内ドキュメント（Qdrant）から関連情報をベクトル検索する。"
                    "プロジェクト固有の仕様・設定・エラー・社内規定・Wikipedia・ニュース記事など"
                    "専門知識が必要な質問に対して使用する。"
                    "collection_name は指定しないこと（システムが自動選択する）。"
                ),
                "input_schema": {
                    "type"      : "object",
                    "properties": {
                        "query": {
                            "type"       : "string",
                            "description": (
                                "検索クエリ。ユーザーの質問から具体的なキーワードを抽出して作成する。"
                                "固有名詞・専門用語は原文のまま含めること。"
                            )
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name"        : "list_rag_collections",
                "description" : "利用可能な Qdrant コレクションの一覧を取得する。",
                "input_schema": {
                    "type"      : "object",
                    "properties": {},
                    "required"  : []
                }
            }
        ]

    def execute_turn(self, user_input: str) -> Generator[Dict[str, Any], None, None]:
        """
        ReAct → Reflection の順にエージェントのターンを実行し、
        進捗状況をイベントとしてyieldするジェネレータ。
        """
        self.thought_log = []
        # [MIGRATION] ターン開始時に会話履歴をリセット（Anthropic はステートレス設計）
        self._messages = []
        logger.info(f"Starting agent turn. Session: {self.session_id}, Input: {user_input[:100]}...")

        # --- Phase 1: ReAct Loop ---
        # ★変更: ハイブリッド検索の状態を表示に追加
        hybrid_status = "有効 (Sparse + Dense)" if self.use_hybrid_search else "無効 (Dense のみ)"
        yield {"type": "log", "content": f"""🤖 **ReAct Phase Start**
📖 **説明**: エージェントが「思考→行動→観察」のサイクルで問題を解決します。
   質問を分析し、必要に応じてツール（検索など）を使用して情報を収集します。
⚡ **ハイブリッド検索**: {hybrid_status}"""}
        draft_answer: Optional[str] = None
        for event in self._execute_react_loop(user_input):
            yield event
            if event["type"] == "final_text":
                draft_answer = event["content"]

        # --- Phase 2: Reflection ---
        if draft_answer:
            yield {"type": "log", "content": """🔄 **Reflection Phase (推敲)**
📖 **説明**: エージェントが作成した回答案を客観的に評価・修正します。
   正確性、適切性、スタイルをチェックして最終回答を作成します。"""}
            final_answer_after_reflection = yield from self._execute_reflection_phase(draft_answer)
            draft_answer = final_answer_after_reflection

        final_answer = self._format_final_answer(draft_answer)
        logger.info(f"Agent turn completed. Final answer length: {len(final_answer)}")
        yield {"type": "final_answer", "content": final_answer}

    def _execute_react_loop(self, user_input: str) -> Generator[Dict[str, Any], None, None]:
        """
        ReAct ループを Anthropic Tool Use 形式で実装。

        generate_with_tools() が返す ToolUseResponse (NamedTuple) を使用し、
        assistant_content の手動再構築は不要。
          - result.assistant_message: response.content をそのまま保持
          - result.tool_calls:        ツール呼び出しリスト
          - result.stop_reason:       ループ継続判定
          - 会話履歴は self._messages で管理（ターン開始時にリセット）
        """
        # --- キーワード抽出とプロンプト拡張 ---
        augmented_input = user_input
        if self.keyword_extractor:
            try:
                keywords = self.keyword_extractor.extract(user_input, top_n=5)
                if keywords:
                    # 抽出キーワードに混入し得る孤立サロゲートを除去してから利用する。
                    keywords_str = _strip_surrogates(", ".join(keywords))
                    augmented_input = (
                        f"{user_input}\n\n"
                        f"【重要: 検索クエリ作成の指示】\n"
                        f"以下の抽出された重要キーワードを、検索クエリに含めてください。\n"
                        f"特に固有名詞・専門用語は原文のまま含めること。\n"
                        f"重要キーワード: {keywords_str}"
                    )
                    logger.info(f"Augmented input with keywords: {keywords_str}")
                    yield {"type": "log", "content": f"""🔑 **Extracted Keywords:** {keywords_str}
📖 **説明**: 質問から重要なキーワードを自動抽出しました。
   これらのキーワードを使って、より正確な検索を行います。"""}
            except Exception as e:
                logger.warning(f"Keyword extraction failed during turn: {e}")

        # [MIGRATION] Anthropic: messages リストで会話履歴を管理
        # Gemini の chat.send_message(augmented_input) に相当する初期化
        self._messages.append({"role": "user", "content": augmented_input})

        max_turns = get_config("agent.max_turns", 10)
        final_text_from_react = ""

        for turn_count in range(1, max_turns + 1):
            logger.debug(f"ReAct turn {turn_count}/{max_turns}")

            # [MIGRATION] LLM 呼び出し（戻り値は ToolUseResponse (NamedTuple)）
            result: ToolUseResponse = self.llm.generate_with_tools(
                messages   = self._messages,
                tools      = self.tools,
                system     = self.system_instruction,
                max_tokens = get_config("agent.max_tokens", 4096),
            )

            # テキスト部分のログ出力（Thought / 通常テキスト）
            if result.text and ("Thought:" in result.text or "考え:" in result.text):
                self.thought_log.append(f"🧠 **Thought:**\n{result.text}")
                yield {"type": "log", "content": f"🧠 **Thought:**\n{result.text}"}

            # ツール呼び出し検出: stop_reason == "tool_use" + tool_calls で判定
            if result.stop_reason != "tool_use" or not result.tool_calls:
                # ツール呼び出しなし → 最終回答
                final_text_from_react = result.text
                break

            # --- ツール呼び出し処理 ---
            # assistant ターンを会話履歴に追記（response.content をそのまま保持）
            self._messages.append(result.assistant_message)

            # 複数ツールの同時呼び出しに対応。全ツール結果を同一 user メッセージにまとめて追記
            tool_results_content: List[Dict[str, Any]] = []

            for tc in result.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["input"]
                tool_id   = tc["id"]

                logger.info(f"Agent Tool Call: {tool_name}({tool_args})")
                self.thought_log.append(f"🛠️ **Tool Call:** `{tool_name}`\nArgs: `{tool_args}`")
                yield {"type": "tool_call", "name": tool_name, "args": tool_args}

                tool_result = ""
                try:
                    if tool_name in TOOLS_MAP:
                        if tool_name == "search_rag_knowledge_base":
                            tool_result = search_rag_knowledge_base_cached(
                                query             = tool_args.get("query", ""),
                                session_id        = self.session_id,
                                collection_name   = tool_args.get("collection_name"),
                                use_hybrid_search = self.use_hybrid_search,
                            )
                        else:
                            tool_result = TOOLS_MAP[tool_name](**tool_args)
                    else:
                        tool_result = f"Error: Tool '{tool_name}' not found."
                except RAGToolError as e:
                    tool_result = f"エラーが発生しました: {str(e)}"
                    logger.error(f"RAG Tool Error during '{tool_name}': {e}")
                except Exception as e:
                    tool_result = f"予期せぬエラー: {str(e)}"
                    logger.error(f"Unexpected error during tool '{tool_name}': {e}", exc_info=True)

                log_tool_result = (
                    str(tool_result)[:500] + "..."
                    if len(str(tool_result)) > 500
                    else str(tool_result)
                )
                self.thought_log.append(f"📝 **Tool Result:**\n{log_tool_result}")
                yield {"type": "tool_result", "content": log_tool_result}
                logger.info(f"Tool Result: {log_tool_result}")

                # NO_RAG_RESULT ログ
                if isinstance(tool_result, str) and tool_result.startswith("[[NO_RAG_RESULT"):
                    reason = "LOW_SCORE" if "LOW_SCORE" in tool_result else "NO_RESULT"
                    log_unanswered_question(
                        query          = user_input,
                        collections    = [tool_args.get("collection_name", "unknown")],
                        reason         = reason,
                        agent_response = "(Search Failed)"
                    )

                # [MIGRATION] Anthropic: tool_result を tool_results_content に蓄積
                # {"type":"tool_result", "tool_use_id":..., "content":...}
                tool_results_content.append({
                    "type"       : "tool_result",
                    "tool_use_id": tool_id,          # LLM が返した id と必ず一致させる
                    "content"    : str(tool_result),
                })

            # [MIGRATION] 全ツール結果を1件の user メッセージとして追記
            self._messages.append({"role": "user", "content": tool_results_content})
            # → 次のループで generate_with_tools() が更新済み messages を受け取る

        yield {"type": "final_text", "content": final_text_from_react}

    def _execute_reflection_phase(self, draft_answer: str) -> Generator[Dict[str, Any], None, str]:
        """
        [MIGRATION] Reflection フェーズを Anthropic 版に書き換え。

        Gemini との主な差異:
          - self.chat.send_message(reflection_msg)
              → self.llm.generate_with_tools(tools=[]) で Tool Use なし呼び出し
          - response.candidates[0].content.parts 走査
              → ToolUseResponse.text を直接利用（走査不要）
          - 会話履歴: self._messages に reflection_msg を追記してコンテキストを維持
        """
        final_response_text = draft_answer
        try:
            reflection_msg = f"{REFLECTION_INSTRUCTION}\n\n**あなたの回答案:**\n{draft_answer}"

            # [MIGRATION] generate_with_tools(tools=[]) を使い、self._messages を全件渡して
            # ReAct ループの検索結果・思考ログを引き継いだまま推敲する（ハルシネーション抑制）。
            self._messages.append({"role": "user", "content": reflection_msg})
            result: ToolUseResponse = self.llm.generate_with_tools(
                messages   = self._messages,
                tools      = [],                # Tool Use なし（Reflection ではツール不要）
                system     = self.system_instruction,
                model      = self.model_name,
                max_tokens = get_config("agent.reflection_max_tokens", 2048),
            )
            reflection_text = result.text

            if not reflection_text:
                logger.warning("Reflection phase did not generate text.")
                return draft_answer

            reflection_thought = ""
            reflection_answer  = ""

            if "Final Answer:" in reflection_text:
                parts = reflection_text.split("Final Answer:", 1)
                reflection_thought = parts[0].strip()
                reflection_answer  = parts[1].strip()
            else:
                reflection_thought = "Format mismatch in reflection."
                reflection_answer  = reflection_text

            if reflection_thought:
                clean_thought = reflection_thought.replace("Thought:", "").strip()
                self.thought_log.append(f"🤔 **Reflection Thought:**\n{clean_thought}")
                logger.info(f"Reflection Thought: {clean_thought}")
                yield {"type": "log", "content": f"""🤔 **Reflection Thought:**
📖 **説明**: エージェントの自己評価の思考プロセスです。
   回答の品質を確認し、必要に応じて修正を行います。

{clean_thought}"""}

            if reflection_answer:
                final_response_text = reflection_answer
                logger.info(f"Reflection Answer: {reflection_answer[:100]}...")

            # Reflection 応答を会話履歴に追記（次回ターンへの引き継ぎ用）
            if reflection_text:
                self._messages.append(result.assistant_message)

        except Exception as e:
            logger.error(f"Error during reflection phase: {e}")
            self.thought_log.append(f"⚠️ **Reflection Error:** {str(e)}")
            yield {"type": "log", "content": f"⚠️ **Reflection Error:** {str(e)}"}
            final_response_text = draft_answer

        return final_response_text

    def _format_final_answer(self, raw_answer: str) -> str:
        """最終回答の整形"""
        if "Answer:" in raw_answer:
            parts = raw_answer.split("Answer:", 1)
            return parts[1].strip()
        elif raw_answer.startswith("Thought:"):
            return raw_answer.replace("Thought:", "").strip()
        elif raw_answer.startswith("考え:"):
            return raw_answer.replace("考え:", "").strip()
        return raw_answer


# Helper function
def get_available_collections_from_qdrant_helper() -> List[str]:
    """Qdrantから利用可能なコレクション名を取得"""
    try:
        # シングルトン QdrantClient を使用（Phase 2 STEP 4 改善）
        client = get_qdrant_client()
        collections = client.get_collections()
        return [c.name for c in collections.collections]
    except Exception as e:
        logger.error(f"Failed to fetch collections: {e}")
        return []
