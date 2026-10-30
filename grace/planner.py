"""
GRACE Planner - 計画生成エージェント
ユーザーの質問から実行計画を生成する。

- GRACE-Support の最初の計画: `create_plan_for_topic(topic)`。0-(A) で整理した 1 話題の
  観点ごとに社内検索（＋利用者が求めた観点は Web 検索）を置き、最後に回答生成を置く。LLM は呼ばない。
- リプラン: `create_plan(query, context_hints=...)`。補足があれば LLM に計画を作らせる。
- 検索が足りるかは実行してみて判断し、足りなければ Web で補う（executor の動的フォールバック）。
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

from services.prompts import SEARCH_QUERY_INSTRUCTION

from .config import GraceConfig, get_config, resolve_heavy_model
from .llm_compat import create_chat_client
from .memory import create_execution_memory
from .schemas import (
    ExecutionPlan,
    PlanStep,
    create_plan_id,
    repair_plan_dependencies,
    validate_plan_dependencies,
)

logger = logging.getLogger(__name__)


# =============================================================================
# プロンプト定義
# =============================================================================

PLAN_GENERATION_PROMPT = f"""
あなたは計画策定の専門家です。ユーザーの質問を分析し、回答を生成するための実行計画を作成してください。

【利用可能なアクション】
- rag_search: ベクトルDB（Qdrant）から関連情報を検索（社内ドキュメント・FAQ向け）
- web_search: Web検索で最新情報や一般的な情報を取得（最新ニュース・外部情報向け）
- reasoning: 収集した情報を分析・統合して回答を生成

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
      rag_search に加えて web_search のステップを計画に含めてよい（rag_search は省かない）

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

        # LLM 計画のプロンプトに出すコレクション一覧の取得役（`_get_available_collections`）
        self._rag_tool = None

        # ⚠️ LLM 計画生成が空応答で倒れたら、以降このインスタンスでは使わない。
        #
        # ローカル LLM が本文を返せない状態（思考だけで枠を使い切る等）は、
        # 同じプロンプトを投げ直しても直らない。にもかかわらずリプランのたびに
        # LLM 計画を試みると、1 回 140〜165 秒 × 2 リトライを毎回捨てる。
        # 実測ではリプラン 3 回で **約 17 分**をこれだけに費やし、結果は
        # 3 回とも同じフォールバック計画だった。
        self._llm_plan_disabled = False

        logger.info(f"Planner initialized with model: {self.model_name}")

    def create_plan(self, query: str, *, context_hints: str = "") -> ExecutionPlan:
        """
        質問から実行計画を生成する（リプラン用。GRACE-Support の最初の計画は `create_plan_for_topic`）。

        - `context_hints` がある（リプラン）か `planner.force_llm_plan` が真: LLM による計画生成
        - それ以外: ルールベースの 2 ステップ計画（rag_search → reasoning。LLM 呼び出しなし）

        どの経路の計画も `_ensure_internal_search` を通すので、**必ず rag_search を含み、
        ask_user を含まない**（検索する前に「検索しない」と決めない）。

        Args:
            query: ユーザーの質問。**これがそのまま検索クエリになる**
            context_hints: リプラン時の補足（前回のエラー・進捗・ユーザー
                フィードバック等）。LLM 計画生成のプロンプトにだけ足され、
                検索クエリには混ざらない。
        Returns:
            ExecutionPlan: 実行計画

        ⚠️ `query` に補足情報を連結して渡してはいけない。**リプランのヒントが
        そのまま rag_search の検索クエリになり、embedding が壊れる**（実測で
        「明日の東京の天気は？\\n\\n【追加情報】\\n注意: 前回の試行で…」が検索
        クエリになっていた）。補足は必ず `context_hints` で渡すこと。
        """
        logger.info(f"Creating execution plan for: {query[:50]}...")
        use_llm = bool(context_hints) or self.config.planner.force_llm_plan
        # LLM 計画生成が一度でも空応答で倒れていたら、以降は試さない。
        # 同じモデル・同じプロンプトなので結果は変わらず、待ち時間だけ増える。
        #
        # ⚠️ getattr で読む。既存テストは `Planner.__new__` で __init__ を
        #    迂回して組み立てるため、属性が無いことがある。
        if use_llm and getattr(self, "_llm_plan_disabled", False):
            logger.info(
                "LLM 計画生成は空応答で無効化済み → ルールベース計画を使用"
                "（同じ結果に 5 分待つのを避ける）"
            )
            use_llm = False
        if use_llm:
            plan = self._create_llm_plan(query, context_hints=context_hints)
        else:
            plan = self._create_rule_based_plan(query)
        return self._ensure_internal_search(plan, query)

    def create_plan_for_topic(self, topic) -> ExecutionPlan:
        """0-(A) で整理した 1 話題から計画を組み立てる（LLM は呼ばない）。

        `topic` は `backend.app.core.question_refiner.Topic`（answer_question / aspects）。

        - 観点ごとに rag_search を 1 つ置く。検索文は `search_query`、社内検索が不足して
          Web で補うときの検索語として `web_query` も持たせる（`PlanStep.web_query`）
        - `wants_latest_web` が真の観点には web_search を置く（検索語は `web_query`）。
          `required=True` なので、社内検索が十分でも executor は飛ばさない
        - 最後に reasoning を置き、回答の質問文は `answer_question`（`original_query`）
        - reasoning は依存を持たない（`depends_on=[]`）。reasoning は成功した全ステップの結果を
          読む（`Executor._prepare_tool_kwargs`）ので依存は要らず、依存を付けると観点の 1 つ
          （例: Web 検索）が失敗しただけで回答そのものが作られなくなる（`_check_dependencies`）
        - 設定で rag_search を禁止しているときは置かない

        ⚠️ 以前は、質問文を文字列で見て（複雑度の加点表・「最新ニュース」などの語）LLM に
        計画を作らせるかを決め、どの検索にも同じ質問文を使っていた。いまは 0-(A) の LLM が
        観点と検索文を決める。複雑度は 0.5 固定で、ReAct へは回さない
        （`Executor.execute(plan, allow_react=False)`）。
        """
        step_timeout = self.config.planner.step_timeout_seconds
        rag_enabled = self._rag_search_enabled()
        steps = []
        for aspect in topic.aspects:
            if rag_enabled:
                steps.append(PlanStep(
                    step_id=len(steps) + 1,
                    action="rag_search",
                    description=f"社内検索: {aspect.name}",
                    query=aspect.search_query,
                    web_query=aspect.web_query,
                    collection=self._prioritized_collection(aspect.search_query),
                    expected_output="関連するドキュメントや情報",
                    fallback="web_search",
                    timeout_seconds=step_timeout,
                ))
            if aspect.wants_latest_web:
                steps.append(PlanStep(
                    step_id=len(steps) + 1,
                    action="web_search",
                    description=f"Web 検索: {aspect.name}",
                    query=aspect.web_query,
                    required=True,
                    expected_output="Web 検索結果",
                    timeout_seconds=step_timeout,
                ))
        steps.append(PlanStep(
            step_id=len(steps) + 1,
            action="reasoning",
            description="取得した情報を元に回答を生成",
            expected_output="ユーザーへの回答",
            timeout_seconds=step_timeout,
        ))
        plan = ExecutionPlan(
            original_query=topic.answer_question,
            complexity=0.5,
            estimated_steps=len(steps),
            requires_confirmation=False,
            steps=steps,
            success_criteria="ユーザーの質問に適切に回答できている",
            plan_id=create_plan_id(),
        )
        logger.info(
            f"Plan from refined topic: {[f'{s.action}:{s.query}' for s in steps if s.query]}"
        )
        return plan

    def _is_excluded(self, collection: str) -> bool:
        """`qdrant.excluded_collections` に部分一致するか。"""
        excluded = getattr(self.config.qdrant, "excluded_collections", None) or []
        return any(keyword in collection for keyword in excluded)

    def _prioritized_collection(self, query: str) -> Optional[str]:
        """P4: 実行メモリの事前分布から、この質問で当たりやすいコレクションを返す。

        十分な実績が無ければ None（=全コレクション検索）を返す。

        ⚠️ **除外対象コレクションは返さない。**

        メモリが返すのは「過去の実績からの**推測**」であって、運用者の明示指定では
        ない。ところがこの戻り値は `PlanStep.collection` に入り、`RAGSearchTool` 側では
        明示指定と区別が付かないため、`qdrant.excluded_collections` を素通りする。
        実測では、誤採用された `wikipedia_ja_5per` が success として記録された結果、
        無関係な質問でも返され続けていた（`collection_priors` はキーワード重複が
        無いと全体集計へフォールバックしていた。2026-10-15 にフォールバックを廃止し、
        重なりが無ければ None にした）。

        除外は運用者が設定した恒久的な意思なので、学習結果より優先する。
        """
        if self._memory is None:
            return None
        try:
            mc = self.config.memory
            # ⚠️ 除外対象は **飛ばして次点を採る**（None で諦めない）。
            #    諦めるとメモリ機構が事実上死ぬ（誤学習が首位に居座ると毎回 None）。
            best = self._memory.best_collection(
                query=query, min_count=mc.min_count, min_score=mc.min_score,
                exclude=self._is_excluded,
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

    def _create_rule_based_plan(self, query: str, complexity: float = 0.5) -> ExecutionPlan:
        """
        ルールベースの標準2ステップ計画を生成（LLM呼び出しなし）

        P4: 実行メモリに十分な実績があれば rag_search の collection を
        事前分布の最良コレクションに固定する（無ければ None=全コレクション検索）。
        """
        prioritized = self._prioritized_collection(query)
        return self._build_rag_reasoning_plan(
            query, complexity=complexity, collection=prioritized
        )

    def _build_plan_prompt(self, query: str, context_hints: str = "") -> str:
        """LLM計画生成用のプロンプトを構築する（利用可能コレクションを埋め込む）。

        `context_hints`（リプランの補足）は **プロンプトにだけ**足す。
        `query` へ連結すると検索クエリまで汚染されるため（`create_plan` 参照）。
        """
        available_collections = self._get_available_collections()
        collections_str = ", ".join(available_collections) if available_collections else "(コレクションなし)"
        prompt = PLAN_GENERATION_PROMPT.format(
            available_collections=collections_str,
            query=query
        )
        if context_hints:
            prompt += (
                "\n\n【前回の試行に関する補足】\n"
                f"{context_hints}\n"
                "この補足は計画の立て方の参考にするだけで、"
                "各ステップの query（検索文）には含めないでください。"
            )
        return prompt + "\n\nIMPORTANT: Ensure the output is a valid, complete JSON object. Do not truncate the response."

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

    def _stamp_llm_plan(self, plan: ExecutionPlan) -> ExecutionPlan:
        """LLM が作った計画に、LLM に決めさせない値を入れる（計画生成・計画修正で共用）。

        - `plan_id`: 新しく振る
        - `created_at`: LLM が `null` を返すと `default_factory` が効かず null のまま残る
          （明示した null は「値あり」として扱われる）。grace_v2 の実測 2026-10-15 で毎回 null だった
        - 各ステップの `timeout_seconds`: `planner.step_timeout_seconds` に揃える。
          LLM の言い値（実測 60）のままだと設定が効かず、同じ質問でもルールベース計画
          （設定値）か LLM 計画かで待ち時間が変わる。リプランも設定値を引き継ぐ
          （`ReplanManager._adjust_step_ids`）
        """
        plan.plan_id = create_plan_id()
        if plan.created_at is None:
            plan.created_at = datetime.now()
        step_timeout = self.config.planner.step_timeout_seconds
        for step in plan.steps:
            step.timeout_seconds = step_timeout
        return plan

    def _finalize_plan(self, plan: ExecutionPlan, complexity: float) -> ExecutionPlan:
        """LLM生成計画に複雑度・plan_id 等を適用し、依存関係を検証してログ出力する。"""
        # 事前に計算した正確な複雑度を適用
        plan.complexity = complexity
        # 計画ID・作成日時・ステップのタイムアウト
        self._stamp_llm_plan(plan)

        # 依存関係を検証し、**実行不能な依存はその場で取り除く**。
        #
        # ⚠️ 以前は警告を出すだけで採用していた。しかし Executor は依存先の
        #    結果が無い限りステップを実行しない（_check_dependencies）ため、
        #    存在しないステップ ID への依存を持つステップは**永久に実行されない**。
        #    それが reasoning ステップだと、回答が一切生成されないまま計画が
        #    「完走」する。ローカル LLM は計画 JSON の精度が低く、実測でも
        #    `Step 4: 存在しない依存先 3` が警告のまま採用され黙って飛ばされていた。
        errors = validate_plan_dependencies(plan)
        if errors:
            logger.warning(f"Plan validation errors: {errors}")
            repairs = repair_plan_dependencies(plan)
            logger.warning(f"Plan repaired (実行不能な依存を除去): {repairs}")

        logger.info(
            f"Plan created: {len(plan.steps)} steps, "
            f"complexity={plan.complexity:.2f}, "
            f"requires_confirmation={plan.requires_confirmation}"
        )
        logger.info(f"Final Execution Plan:\n{plan.model_dump_json(indent=2)}")
        return plan

    def _create_llm_plan(self, query: str, *, context_hints: str = "") -> ExecutionPlan:
        """
        質問から実行計画を生成（LLM使用版 - 本来のロジック）
        Args:
            query: ユーザーの質問
            context_hints: リプランの補足（プロンプトにだけ足す）
        Returns:
            ExecutionPlan: LLMが生成した実行計画（失敗時はフォールバック計画）
        """
        logger.info(f"Creating LLM execution plan for: {query[:50]}...")

        try:
            # プロンプトを構築
            prompt = self._build_plan_prompt(query, context_hints)

            # --- [IPO LOG] PROCESS INPUT (GRACE PLANNER) ---
            logger.info(f"\n{'=' * 20} [GRACE PLANNER IPO: INPUT] {'=' * 20}\n{prompt}\n{'=' * 60}")

            # リトライ付きでLLM呼び出し。空レスポンス・不完全JSONはリトライする。
            plan = self._generate_plan_with_retry(
                prompt,
                label="create_plan LLM",
                max_output_tokens=self.config.planner.plan_max_output_tokens,
                log_output=True,
            )

            return self._finalize_plan(plan, plan.complexity)

        except Exception as e:
            logger.error(f"Failed to create plan with LLM: {e}")
            # 以降のリプランでは LLM 計画を試みない。モデルが本文を返せない
            # 状態は投げ直しても直らず、1 リプランあたり 5 分前後を捨てるだけ。
            self._llm_plan_disabled = True
            logger.info(
                "Falling back to simple plan（以降の再計画では LLM 計画生成を行いません）"
            )
            return self._create_fallback_plan(query)

    def _get_available_collections(self) -> list[str]:
        """rag_search が実際に検索するコレクションを、検索する順に返す。

        LLM 計画のプロンプト（【利用可能なコレクション】）に出す一覧。
        `RAGSearchTool.search_candidates()` をそのまま使うので、検索時と同じく
        次元違い・空のコレクションは出ず、除外リスト（`qdrant.excluded_collections`）も
        当たる（業界プロファイルの許可リストで名指しされたものは残る）。

        ⚠️ 以前は Qdrant の全件を返していた。除外対象や次元違いのコレクションまで
        LLM に見せており、LLM が名指しすると `PlanStep.collection` に入って、
        RAGSearchTool から「明示指定」に見えて除外を素通りする。Qdrant に繋がらない
        ときは `search_priority`（実在しないキーワード）を返していたが、これも
        LLM に見せる名前ではない。

        Returns:
            コレクション名のリスト。Qdrant に繋がらなければ空
        """
        try:
            if getattr(self, "_rag_tool", None) is None:
                from .tools import RAGSearchTool

                self._rag_tool = RAGSearchTool(config=self.config)
            return self._rag_tool.search_candidates() or []
        except Exception as e:
            logger.warning(f"Failed to get collections: {e}")
            return []

    def _rag_search_enabled(self) -> bool:
        tools = self.config.tools
        return "rag_search" in (tools.enabled or []) and "rag_search" not in (tools.disabled or [])

    def _ensure_internal_search(self, plan: ExecutionPlan, query: str) -> ExecutionPlan:
        """計画に**必ず社内検索（rag_search）を入れ**、ask_user を外す（全経路の後処理）。

        ⚠️ **検索する前に「検索しない」と決めない。** 2026-10-28 まで、`create_plan` は
        「その件」等を含む質問を曖昧と判定し、rag_search を含まない ask_user だけの計画を
        返していた。その聞き返しは画面につながっておらず（Support の入口は
        `on_intervention_required` を渡さない）、「検索もしない・聞き返しもしない」行き止まり
        だった。検索で当たらなければ、後段（Web で補う・④' 情報なし → 有人対応）が扱う。

        - **ask_user を外す**: 画面に出せない聞き返しを計画に入れない。依存も外す
        - **rag_search が無ければ先頭に足す**: LLM 計画は省くことがある（grace_v2 の実測 2026-10-15:
          「最新ニュースを検索して…」で web_search → reasoning だけ）。質問文をそのまま
          検索文にし、reasoning はその結果にも依存させる
        - **reasoning が無ければ末尾に足す**（ask_user だけの計画だった場合）
        - 外したり足したりしたら、ステップ ID を 1 から振り直す

        設定で rag_search を禁止しているときは足さない（禁止を素通りしない）。
        `tests/test_plan_always_searches.py`。
        """
        removed = {s.step_id for s in plan.steps if s.action == "ask_user"}
        steps = [s for s in plan.steps if s.action != "ask_user"]
        if removed:
            logger.info(f"[plan] ask_user を外しました（画面に出せない聞き返し）: steps={sorted(removed)}")
            for s in steps:
                s.depends_on = [d for d in s.depends_on if d not in removed]

        step_timeout = self.config.planner.step_timeout_seconds
        next_id = max([s.step_id for s in plan.steps], default=0) + 1
        if self._rag_search_enabled() and not any(s.action == "rag_search" for s in steps):
            rag = PlanStep(
                step_id=next_id,
                action="rag_search",
                description="関連情報を検索",
                query=query,
                collection=self._prioritized_collection(query),
                expected_output="関連するドキュメントや情報",
                fallback="web_search",
                timeout_seconds=step_timeout,
            )
            next_id += 1
            for s in steps:
                if s.action == "reasoning":
                    s.depends_on = [rag.step_id, *s.depends_on]
            steps = [rag, *steps]
            logger.info("[plan] rag_search が無いので先頭に足しました（社内検索を必ず行う）")

        if not any(s.action == "reasoning" for s in steps):
            steps.append(PlanStep(
                step_id=next_id,
                action="reasoning",
                description="取得した情報を元に回答を生成",
                depends_on=[s.step_id for s in steps],
                expected_output="ユーザーへの回答",
                timeout_seconds=step_timeout,
            ))

        if [s.step_id for s in steps] != [s.step_id for s in plan.steps]:
            renumber = {s.step_id: i for i, s in enumerate(steps, start=1)}
            for s in steps:
                s.step_id = renumber[s.step_id]
                s.depends_on = [renumber[d] for d in s.depends_on if d in renumber]
            plan.steps = steps
            plan.estimated_steps = len(steps)
        return plan

    def _create_fallback_plan(self, query: str) -> ExecutionPlan:
        """
        フォールバック用の単純な計画を生成

        Args:
            query: ユーザーの質問

        Returns:
            ExecutionPlan: 単純な2ステップ計画
        """
        logger.info("Creating fallback plan")

        # P4: 実行メモリの事前分布を優先。無ければ None（＝ RAGSearchTool が全件を順に検索）。
        #
        # ⚠️ 以前は名前に "wikipedia" を含むコレクションを先頭に固定していた。
        #    wikipedia は除外リストに入っているのに、`PlanStep.collection` に入ると
        #    RAGSearchTool から「明示指定」に見えて除外を素通りしていた。
        #    検索順は RAGSearchTool（search_priority・許可リスト）に任せる。
        return self._build_rag_reasoning_plan(
            query,
            complexity=0.5,
            collection=self._prioritized_collection(query),
            rag_description="全コレクションから関連情報を検索",
        )

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
            # 計画ID・作成日時・ステップのタイムアウト（計画生成と同じ）
            self._stamp_llm_plan(refined_plan)

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
