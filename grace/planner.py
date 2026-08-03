"""
GRACE Planner - 計画生成エージェント
ユーザーの質問を分析し、実行計画を生成
重要：「計画はシンプルに 2 ステップ固定 → 検索が足りるかは実行してみて判断
　　　　→ 足りなければ Web、それでもダメなら人に聞く、という動的フォールバック方式」
"""

import json
import logging
import re
import time
from typing import Optional

from qdrant_client import QdrantClient

from services.prompts import SEARCH_QUERY_INSTRUCTION
from services.qdrant_service import get_all_collections

from .config import GraceConfig, get_config, heavy_thinking_budget, resolve_heavy_model
from .llm_compat import create_chat_client
from .memory import create_execution_memory
from .schemas import (
    ExecutionPlan,
    PlanStep,
    create_plan_id,
    validate_plan_dependencies,
)

logger = logging.getLogger(__name__)


# 指示語で対象が未特定の「曖昧クエリ」を表すパターン（例: 「あの件について教えて」）。
_AMBIGUOUS_REFERENT_PATTERNS = (
    "あの件", "その件", "この件", "例の件", "あの話", "その話", "例の話",
    "あれについて", "それについて", "あの問題", "その問題",
)
# 対象が曖昧になりやすい指示語（単独では曖昧と断定しない。具体的手がかりが無い場合のみ）。
_DEMONSTRATIVES = ("あの", "その", "あれ", "それ", "例の", "先日の", "この間の")

# ヒューリスティック複雑度推定（estimate_complexity）のキーワード別加点表。
# ベーススコア 0.5 に対し、出現したキーワードの重みを加算する。
_COMPLEXITY_FACTORS = (
    ("比較", 0.15),
    ("違い", 0.15),
    ("複数", 0.2),
    ("最新", 0.1),
    ("理由", 0.1),
    ("方法", 0.1),
    ("詳しく", 0.15),
    ("ステップ", 0.1),
    ("手順", 0.1),
    ("なぜ", 0.1),
    ("どのように", 0.15),
)


def is_ambiguous_query(query: str) -> bool:
    """指示語のみで対象が特定できない「曖昧クエリ」かどうかを判定する。

    例: 「あの件について詳しく教えて」→ True（何の件か不明）。
    一方、固有名詞・数字・カタカナ語などの具体的手がかりを含むクエリは False。
    曖昧クエリは検索しても無関係チャンクが当たるだけなので、プランナー段で検知して
    ask_user（確認）経路へ振り分ける。
    """
    q = (query or "").strip()
    if not q:
        return True
    # 1. 「あの件」等、未解決の指示対象を明示するパターン
    if any(p in q for p in _AMBIGUOUS_REFERENT_PATTERNS):
        return True
    # 2. 指示語を含み、かつ具体的な手がかり（英数字 / 3文字以上のカタカナ語）が無く短い
    has_concrete = bool(re.search(r"[A-Za-z0-9０-９]", q)) or bool(re.search(r"[ァ-ヴ]{3,}", q))
    has_demonstrative = any(d in q for d in _DEMONSTRATIVES)
    if has_demonstrative and not has_concrete and len(q) <= 30:
        return True
    return False


# =============================================================================
# プロンプト定義
# =============================================================================

PLAN_GENERATION_PROMPT = f"""
あなたは計画策定の専門家です。ユーザーの質問を分析し、回答を生成するための実行計画を作成してください。

【利用可能なアクション】
- rag_search: ベクトルDB（Qdrant）から関連情報を検索（社内ドキュメント・FAQ向け）
- web_search: Web検索で最新情報や一般的な情報を取得（最新ニュース・外部情報向け）
- reasoning: 収集した情報を分析・統合して回答を生成
- ask_user: ユーザーに追加情報や確認を求める

【利用可能なコレクション (rag_search用)】
{{available_collections}}

【コレクション選択のルール (重要)】
- `rag_search` の `collection` 引数は、原則として指定しないでください（`null` または省略）。
   * 特定のコレクション（例: wikipedia_ja）に限定せず、利用可能なすべてのコレクションから網羅的に検索を行うためです。
   * システム側で自動的に最適なコレクション順序で検索を実行します。
- 例外: ユーザーが明示的に「livedoorニュースから検索して」のように指定した場合のみ、そのコレクション名を指定してください。

【検索クエリの作成ルール】
- `rag_search` の `query` 引数は、ユーザーの質問文を極力そのまま使用してください。
   * 単語の羅列（例: "金色夜叉 尾崎紅葉"）に変換せず、自然言語の文脈
   （例:"〜の構成者は誰ですか？"）を維持することで、ベクトル検索の精度が向上します。

【計画作成のルール (厳守)】
1. 検索アクション（rag_search）は、可能な限り「1つのステップ」にまとめてください。
    * 質問を分解して複数の検索ステップを作らないでください。
2. `rag_search` の `query` は、ユーザーの元の質問文を「完全一致でコピー」してください。
    * 要約、キーワード化、分割は一切禁止です。
    * 悪い例: "金色夜叉 構成者"
    * 良い例: "『金色夜叉:尾崎紅葉不如帰:徳富蘆花』の構成者は誰ですか？"
3. 依存関係を正しく設定してください（depends_onは先行ステップのIDのみ）。
4. 失敗時の代替手段（fallback）を検討してください。
5. 最後のステップは必ず "reasoning" で回答を生成してください
6. rag_search と web_search の使い分け:
    * 計画には web_search ステップを含めないでください
    * web_search は、rag_search の結果が不十分な場合に executor が自動的に実行します
    * 計画は常に rag_search → reasoning の2ステップ構成としてください
    * rag_search の fallback には "web_search" を指定してください
    * 例外: ユーザーが明示的に「最新ニュースを検索して」等と指示した場合のみ、
      web_search 単体のステップを計画に含めてよい

{SEARCH_QUERY_INSTRUCTION}

【計画の複雑度(complexity)の目安】
- 0.0-0.3: 単純な質問（1-2ステップ）
- 0.4-0.6: 中程度の質問（2-3ステップ）
- 0.7-1.0: 複雑な質問（4ステップ以上）

【requires_confirmationをtrueにする条件】
- 質問が曖昧で複数の解釈が可能な場合
- 実行に時間がかかる可能性がある場合
- 外部リソースへのアクセスが必要な場合

ユーザーの質問: {{query}}

JSON形式で実行計画を出力してください。
"""

COMPLEXITY_ESTIMATION_PROMPT = """
以下の質問の複雑度を0.0から1.0の数値で評価してください。

評価基準:
- 0.0-0.2: 非常に単純（事実確認、定義の質問）
- 0.3-0.4: 単純（1つのトピックについての説明）
- 0.5-0.6: 中程度（比較、分析が必要）
- 0.7-0.8: 複雑（複数のソースからの情報統合が必要）
- 0.9-1.0: 非常に複雑（専門知識、多段階の推論が必要）

質問: {query}

数値のみを回答してください（例: 0.5）
"""


# =============================================================================
# Planner クラス
# =============================================================================

class Planner:
    """計画生成エージェント"""

    def __init__(
            self,
            config: Optional[GraceConfig] = None,
            model_name: Optional[str] = None
    ):
        """
        Args:
            config: GRACE設定（Noneの場合はデフォルト設定を使用）
            model_name: 使用するモデル名（Noneの場合は設定から取得）
        """
        self.config = config or get_config()
        # M-1: 計画生成は論理層。heavy_model 未設定なら llm.model と同じ。
        self.model_name = model_name or resolve_heavy_model(self.config)
        self.client = create_chat_client(self.config)

        # P4: 実行メモリ層（コレクション事前分布の学習・反映）
        self._memory = None
        if getattr(self.config, "memory", None) and self.config.memory.enabled:
            self._memory = create_execution_memory(self.config.memory.path)

        logger.info(f"Planner initialized with model: {self.model_name}")

    # LLM計画生成を強制するクエリマーカー（明示的なWeb検索指示など）
    _LLM_PLAN_MARKERS = (
        "最新ニュース", "ニュースを検索", "web検索", "ウェブ検索", "webで検索",
    )

    def create_plan(self, query: str) -> ExecutionPlan:
        """
        質問から実行計画を生成（二層方式）

        - 通常のクエリ: ルールベースの2ステップ計画を即時生成（LLM呼び出しなし）
        - 複雑なクエリ / 明示的なWeb検索指示: LLMによる計画生成

        Args:
            query: ユーザーの質問
        Returns:
            ExecutionPlan: 実行計画
        """
        logger.info(f"Creating execution plan for: {query[:50]}...")

        # 曖昧クエリ（指示語のみで対象不明）は検索しても無関係チャンクが当たるだけ
        # なので、ask_user（確認）経路へ振り分ける。
        if is_ambiguous_query(query):
            logger.info("Ambiguous query detected → clarification (ask_user) plan")
            return self._create_clarification_plan(query)

        # ヒューリスティック（非LLM）複雑度で二層判定
        heuristic_complexity = self.estimate_complexity(query)

        if not self._should_use_llm_plan(query, heuristic_complexity):
            logger.info(
                f"Using rule-based plan (complexity={heuristic_complexity:.2f} < "
                f"{self.config.planner.llm_plan_complexity_threshold})"
            )
            return self._create_rule_based_plan(query, heuristic_complexity)

        return self._create_llm_plan(query)

    def _should_use_llm_plan(self, query: str, heuristic_complexity: float) -> bool:
        """LLM計画生成を使用すべきか判定する"""
        if self.config.planner.force_llm_plan:
            return True

        query_lower = query.lower()
        if any(marker in query_lower for marker in self._LLM_PLAN_MARKERS):
            return True

        return heuristic_complexity >= self.config.planner.llm_plan_complexity_threshold

    def _prioritized_collection(self, query: str) -> Optional[str]:
        """P4: 実行メモリの事前分布から、この質問で当たりやすいコレクションを返す。

        十分な実績が無ければ None（=全コレクション検索）を返す。
        """
        if self._memory is None:
            return None
        try:
            mc = self.config.memory
            best = self._memory.best_collection(
                query=query, min_count=mc.min_count, min_score=mc.min_score
            )
            if best:
                logger.info(f"[memory] prioritized collection for query: {best}")
            return best
        except Exception as e:
            logger.warning(f"_prioritized_collection failed: {e}")
            return None

    def _build_rag_reasoning_plan(
            self,
            query: str,
            *,
            complexity: float,
            collection: Optional[str],
            rag_description: str = "関連情報を検索",
    ) -> ExecutionPlan:
        """
        rag_search → reasoning の標準2ステップ計画を組み立てる共通ビルダー。

        ルールベース計画・フォールバック計画はいずれもこの構造を共有する。
        rag_search は collection（実行メモリの優先コレクション or None=全コレクション
        検索）と fallback=web_search を持ち、Executor 側の動的フォールバック連鎖
        （web_search / ask_user）がそのまま機能する。

        Args:
            query: ユーザーの質問
            complexity: 計画に記録する複雑度
            collection: rag_search の対象コレクション（None=全コレクション検索）
            rag_description: rag_search ステップの description
        Returns:
            ExecutionPlan: 標準2ステップ計画
        """
        step_timeout = self.config.planner.step_timeout_seconds
        return ExecutionPlan(
            original_query=query,
            complexity=complexity,
            estimated_steps=2,
            requires_confirmation=False,
            steps=[
                PlanStep(
                    step_id=1,
                    action="rag_search",
                    description=rag_description,
                    query=query,
                    collection=collection,
                    expected_output="関連するドキュメントや情報",
                    fallback="web_search",
                    timeout_seconds=step_timeout
                ),
                PlanStep(
                    step_id=2,
                    action="reasoning",
                    description="取得した情報を元に回答を生成",
                    query=None,
                    collection=None,
                    depends_on=[1],
                    expected_output="ユーザーへの回答",
                    fallback=None,
                    timeout_seconds=step_timeout
                )
            ],
            success_criteria="ユーザーの質問に適切に回答できている",
            plan_id=create_plan_id()
        )

    def _create_rule_based_plan(self, query: str, complexity: float) -> ExecutionPlan:
        """
        ルールベースの標準2ステップ計画を生成（LLM呼び出しなし）

        P4: 実行メモリに十分な実績があれば rag_search の collection を
        事前分布の最良コレクションに固定する（無ければ None=全コレクション検索）。
        """
        prioritized = self._prioritized_collection(query)
        return self._build_rag_reasoning_plan(
            query, complexity=complexity, collection=prioritized
        )

    def _build_plan_prompt(self, query: str) -> str:
        """LLM計画生成用のプロンプトを構築する（利用可能コレクションを埋め込む）。"""
        available_collections = self._get_available_collections()
        collections_str = ", ".join(available_collections) if available_collections else "(コレクションなし)"
        return PLAN_GENERATION_PROMPT.format(
            available_collections=collections_str,
            query=query
        ) + "\n\nIMPORTANT: Ensure the output is a valid, complete JSON object. Do not truncate the response."

    def _generate_plan_with_retry(
            self,
            prompt: str,
            *,
            label: str,
            max_output_tokens: Optional[int] = None,
            log_output: bool = False,
    ) -> ExecutionPlan:
        """
        structured-output（JSON）で ExecutionPlan を生成する共通ヘルパー。

        空レスポンス・不完全JSONを検知してリトライし、全試行失敗時は最後の
        例外を送出する。計画生成（_create_llm_plan）と計画修正（refine_plan）の
        双方がこのヘルパーを共有し、リトライ・ガードの挙動を揃える。

        Args:
            prompt: LLMへ渡すプロンプト
            label: ログ用ラベル（例 "create_plan LLM"）
            max_output_tokens: 最大出力トークン数（None の場合は指定しない）
            log_output: True の場合、各試行のレスポンス本文をIPOログ出力する
        Returns:
            ExecutionPlan: パース済みの実行計画
        Raises:
            Exception: 全リトライが失敗した場合、最後に発生した例外
        """
        config = {
            "response_mime_type": "application/json",
            # response_schema には Pydantic クラスを直接渡す
            "response_schema": ExecutionPlan,
            "temperature": self.config.llm.temperature,
        }
        if max_output_tokens is not None:
            config["max_output_tokens"] = max_output_tokens
        # M-1: 論理層の拡張思考（heavy_model 設定時のみ有効。既定 0=無効）
        config["thinking_budget_tokens"] = heavy_thinking_budget(self.config)

        max_attempts = self.config.planner.llm_plan_max_attempts
        last_error = None

        for attempt in range(max_attempts):
            try:
                t0 = time.time()
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )
                elapsed = time.time() - t0
                logger.info(f"[API時間] {label} (attempt {attempt + 1}/{max_attempts}): {elapsed:.1f}秒")

                if log_output:
                    # --- [IPO LOG] PROCESS OUTPUT (GRACE PLANNER) ---
                    logger.info(f"\n{'=' * 20} [GRACE PLANNER IPO: OUTPUT] {'=' * 20}\n{response.text}\n{'=' * 60}")

                # 空レスポンスガード
                if not response or not response.text:
                    logger.warning(f"{label}: empty response (attempt {attempt + 1}/{max_attempts})")
                    continue

                # JSON完全性チェック（EOF検知）
                try:
                    json.loads(response.text)
                except json.JSONDecodeError as je:
                    logger.warning(f"{label}: incomplete/invalid JSON (attempt {attempt + 1}/{max_attempts}): {je}")
                    continue  # リトライ

                # JSONをパースしてExecutionPlanに変換
                return ExecutionPlan.model_validate_json(response.text)

            except Exception as e:
                last_error = e
                logger.warning(f"{label} attempt {attempt + 1}/{max_attempts} failed: {e}")
                continue

        raise last_error or ValueError(f"{label}: failed after {max_attempts} retries")

    def _finalize_plan(self, plan: ExecutionPlan, complexity: float) -> ExecutionPlan:
        """LLM生成計画に複雑度・plan_id を適用し、依存関係を検証してログ出力する。"""
        # 事前に計算した正確な複雑度を適用
        plan.complexity = complexity
        # 計画IDを設定
        plan.plan_id = create_plan_id()

        # 依存関係を検証（エラーがあってもフォールバックせず、警告のみ）
        errors = validate_plan_dependencies(plan)
        if errors:
            logger.warning(f"Plan validation errors: {errors}")

        logger.info(
            f"Plan created: {len(plan.steps)} steps, "
            f"complexity={plan.complexity:.2f}, "
            f"requires_confirmation={plan.requires_confirmation}"
        )
        logger.info(f"Final Execution Plan:\n{plan.model_dump_json(indent=2)}")
        return plan

    def _create_llm_plan(self, query: str) -> ExecutionPlan:
        """
        質問から実行計画を生成（LLM使用版 - 本来のロジック）
        Args:
            query: ユーザーの質問
        Returns:
            ExecutionPlan: LLMが生成した実行計画（失敗時はフォールバック計画）
        """
        logger.info(f"Creating LLM execution plan for: {query[:50]}...")

        try:
            # 複雑度を推定 (LLMを使用)
            estimated_complexity = self.estimate_complexity_with_llm(query)

            # プロンプトを構築
            prompt = self._build_plan_prompt(query)

            # --- [IPO LOG] PROCESS INPUT (GRACE PLANNER) ---
            logger.info(f"\n{'=' * 20} [GRACE PLANNER IPO: INPUT] {'=' * 20}\n{prompt}\n{'=' * 60}")

            # リトライ付きでLLM呼び出し。空レスポンス・不完全JSONはリトライする。
            plan = self._generate_plan_with_retry(
                prompt,
                label="create_plan LLM",
                max_output_tokens=self.config.planner.plan_max_output_tokens,
                log_output=True,
            )

            return self._finalize_plan(plan, estimated_complexity)

        except Exception as e:
            logger.error(f"Failed to create plan with LLM: {e}")
            logger.info("Falling back to simple plan")
            return self._create_fallback_plan(query)

    def _get_available_collections(self) -> list[str]:
        """利用可能なQdrantコレクションを取得"""
        try:
            client = QdrantClient(url=self.config.qdrant.url)
            cols = get_all_collections(client)
            return [c["name"] for c in cols]
        except Exception as e:
            logger.warning(f"Failed to get collections: {e}")
            return self.config.qdrant.search_priority  # デフォルトリストを返す

    def _create_clarification_plan(self, query: str) -> ExecutionPlan:
        """曖昧クエリに対する確認（ask_user）計画を生成する。

        検索・推論は行わず、ユーザーに対象の明確化を求める単一 ask_user ステップ。
        requires_confirmation=True とし、Executor 側で最終回答なし（明確化要求）→
        低信頼（ESCALATE 帯）として介入レベルが CONFIRM/ESCALATE になる。
        """
        return ExecutionPlan(
            original_query=query,
            complexity=0.2,
            estimated_steps=1,
            requires_confirmation=True,
            steps=[
                PlanStep(
                    step_id=1,
                    action="ask_user",
                    description="質問が曖昧なため、対象の明確化を求める",
                    query=(
                        "ご質問の対象が特定できませんでした。"
                        "どの件・どのトピックについてか、具体的に教えてください。"
                    ),
                    collection=None,
                    expected_output="ユーザーによる質問の明確化",
                    fallback=None,
                    timeout_seconds=self.config.planner.step_timeout_seconds,
                )
            ],
            success_criteria="曖昧な質問に対し、確認（明確化）を求められていること",
            plan_id=create_plan_id(),
        )

    def _create_fallback_plan(self, query: str) -> ExecutionPlan:
        """
        フォールバック用の単純な計画を生成

        Args:
            query: ユーザーの質問

        Returns:
            ExecutionPlan: 単純な2ステップ計画
        """
        logger.info("Creating fallback plan")

        # P4: 実行メモリの事前分布を優先。無ければ動的取得（失敗時はNone＝自動選択）。
        fallback_collection = self._prioritized_collection(query)
        if fallback_collection is None:
            try:
                available = self._get_available_collections()
                fallback_collection = next(
                    (c for c in available if "wikipedia" in c), None
                )
            except Exception:
                fallback_collection = None

        return self._build_rag_reasoning_plan(
            query,
            complexity=0.5,
            collection=fallback_collection,
            rag_description="全コレクションから関連情報を検索",
        )

    def estimate_complexity(self, query: str) -> float:
        """
        質問の複雑度を推定（0.0-1.0）
        Args:
            query: ユーザーの質問
        Returns:
            float: 複雑度スコア
        """
        # キーワードベースの簡易推定（加点表は _COMPLEXITY_FACTORS）
        score = 0.5  # ベーススコア

        for keyword, weight in _COMPLEXITY_FACTORS:
            if keyword in query:
                score += weight

        # 質問の長さも考慮
        if len(query) > 100:
            score += 0.1
        if len(query) > 200:
            score += 0.1

        return min(1.0, score)

    def estimate_complexity_with_llm(self, query: str) -> float:
        """
        LLMを使用して質問の複雑度を推定
        Args:
            query: ユーザーの質問
        Returns:
            float: 複雑度スコア
        """
        try:
            prompt = COMPLEXITY_ESTIMATION_PROMPT.format(query=query)

            t0 = time.time()
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={
                    "temperature": self.config.planner.complexity_temperature,
                    "max_output_tokens": self.config.planner.complexity_max_output_tokens,
                }
            )
            elapsed = time.time() - t0
            logger.info(f"[API時間] estimate_complexity_with_llm: {elapsed:.1f}秒")

            # Noneガード: AFC永続化により response.text が None になることがある
            if not response or not response.text:
                logger.warning("estimate_complexity_with_llm: empty response")
                return self.estimate_complexity(query)

            complexity = float(response.text.strip())
            return min(1.0, max(0.0, complexity))

        except Exception as e:
            logger.warning(f"LLM complexity estimation failed: {e}")
            return self.estimate_complexity(query)

    def refine_plan(
            self,
            plan: ExecutionPlan,
            feedback: str
    ) -> ExecutionPlan:
        """
        フィードバックに基づいて計画を修正
        Args:
            plan: 元の計画
            feedback: ユーザーからのフィードバック
        Returns:
            ExecutionPlan: 修正された計画
        """
        logger.info(f"Refining plan {plan.plan_id} with feedback")

        refine_prompt = f"""
以下の実行計画をユーザーのフィードバックに基づいて修正してください。

【元の計画】
クエリ: {plan.original_query}
ステップ数: {len(plan.steps)}
ステップ: {[s.description for s in plan.steps]}

【ユーザーのフィードバック】
{feedback}

修正された計画をJSON形式で出力してください。
"""

        try:
            # 計画生成と同じリトライ・空/JSONガードを共有する
            refined_plan = self._generate_plan_with_retry(
                refine_prompt,
                label="refine_plan LLM",
            )
            refined_plan.plan_id = create_plan_id()

            logger.info(f"Plan refined: {refined_plan.plan_id}")
            return refined_plan

        except Exception as e:
            logger.error(f"Failed to refine plan: {e}")
            return plan


# =============================================================================
# ファクトリ関数
# =============================================================================

def create_planner(
        config: Optional[GraceConfig] = None,
        model_name: Optional[str] = None
) -> Planner:
    """
    Plannerインスタンスを作成
    Args:
        config: GRACE設定
        model_name: 使用するモデル名
    Returns:
        Planner: Plannerインスタンス
    """
    return Planner(config=config, model_name=model_name)


# =============================================================================
# エクスポート
# =============================================================================

__all__ = [
    "Planner",
    "create_planner",
    "PLAN_GENERATION_PROMPT",
]
