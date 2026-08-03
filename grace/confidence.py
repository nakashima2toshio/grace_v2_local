"""
GRACE Confidence - 信頼度計算システム

ハイブリッド方式（重み付き平均 + LLM自己評価）による
多軸信頼度計算を実装
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from google import genai  # embedding 専用（SourceAgreementCalculator の embed_content）
from pydantic import BaseModel, Field

from .config import GraceConfig, get_config, heavy_thinking_budget, resolve_heavy_model
from .llm_compat import create_chat_client

logger = logging.getLogger(__name__)


# =============================================================================
# 信頼度要素
# =============================================================================
# Gemini Structured Output用スキーマ
class EvaluationResult(BaseModel):
    """LLM信頼度評価の応答スキーマ"""
    score: float
    reason: str


@dataclass
class ConfidenceFactors:
    """信頼度を構成する各要素"""

    # RAG検索関連
    search_result_count: int = 0  # 検索結果数
    search_avg_score: float = 0.0  # 平均類似度スコア
    search_max_score: float = 0.0  # 最高類似度スコア
    search_score_variance: float = 1.0  # スコアの分散（低いほど一貫性あり）

    # 複数ソース関連
    source_agreement: float = 0.0  # 情報源間の一致度 (0-1)
    source_count: int = 0  # 引用ソース数

    # LLM自己評価
    llm_self_confidence: float = 0.5  # LLMの自己評価 (0-1)

    # 根拠妥当性（S1: groundedness）— 最終回答の各主張が引用ソースに支持される割合
    groundedness: float = 0.0  # 支持率 (0-1)。0 は未検証/未算出を含む

    # ツール実行関連
    tool_success_rate: float = 1.0  # ツール成功率
    tool_execution_count: int = 0  # 実行ツール数
    tool_success_count: int = 0  # 成功ツール数

    # クエリ関連
    query_coverage: float = 0.0  # クエリへの回答網羅度

    # ステップタイプ
    is_search_step: bool = False  # 検索ステップかどうか


@dataclass
class ConfidenceScore:
    """信頼度スコアと内訳"""

    score: float  # 最終スコア (0.0-1.0)
    factors: ConfidenceFactors  # 計算に使用した要素
    breakdown: Dict[str, float] = field(default_factory=dict)  # 各要素のスコア内訳
    penalties_applied: List[str] = field(default_factory=list)  # 適用されたペナルティ
    reason: str = ""  # 信頼度スコアの理由（LLM評価などで使用）

    @property
    def level(self) -> str:
        """信頼度レベルを取得"""
        if self.score >= 0.9:
            return "high"
        elif self.score >= 0.7:
            return "medium"
        elif self.score >= 0.4:
            return "low"
        else:
            return "very_low"


# =============================================================================
# 介入レベル
# =============================================================================

class InterventionLevel(str, Enum):
    """介入レベル"""
    SILENT = "silent"  # バックグラウンドで進行
    NOTIFY = "notify"  # ステータス表示
    CONFIRM = "confirm"  # 確認を求める
    ESCALATE = "escalate"  # ユーザー入力を要求


@dataclass
class ActionDecision:
    """信頼度に基づくアクション決定"""

    level: InterventionLevel
    confidence_score: float
    reason: str
    suggested_action: Optional[str] = None

    @property
    def should_proceed(self) -> bool:
        """自動進行可能か"""
        return self.level in [InterventionLevel.SILENT, InterventionLevel.NOTIFY]

    @property
    def needs_confirmation(self) -> bool:
        """確認が必要か"""
        return self.level == InterventionLevel.CONFIRM

    @property
    def needs_user_input(self) -> bool:
        """ユーザー入力が必要か"""
        return self.level == InterventionLevel.ESCALATE


# =============================================================================
# Confidence Calculator
# =============================================================================

class ConfidenceCalculator:
    """ハイブリッド方式によるConfidence計算"""

    def __init__(self, config: Optional[GraceConfig] = None):
        self.config = config or get_config()
        self.weights = self.config.confidence.weights
        self._validate_weights()
        logger.info("ConfidenceCalculator initialized")

    def _validate_weights(self):
        """重みの合計が1.0であることを確認"""
        total = (
                self.weights.search_quality +
                self.weights.source_agreement +
                self.weights.llm_self_eval +
                self.weights.tool_success +
                self.weights.query_coverage
        )
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {total}")

    def calculate(self, factors: ConfidenceFactors) -> ConfidenceScore:
        """ハイブリッドConfidence計算"""
        breakdown = {}
        penalties = []

        search_quality = self._calc_search_quality(factors)
        breakdown["search_quality"] = search_quality
        source_agreement = factors.source_agreement
        breakdown["source_agreement"] = source_agreement
        llm_self_eval = factors.llm_self_confidence
        breakdown["llm_self_eval"] = llm_self_eval
        tool_success = self._calc_tool_success(factors)
        breakdown["tool_success"] = tool_success
        query_coverage = factors.query_coverage
        breakdown["query_coverage"] = query_coverage

        if factors.is_search_step:
            base_score = search_quality
            if tool_success < 1.0:
                base_score *= tool_success
            breakdown["llm_self_eval"] = 0.0
            breakdown["query_coverage"] = 0.0
        else:
            valid_weights = 0.0
            weighted_sum = 0.0
            if search_quality > 0:
                w = 0.6
                weighted_sum += search_quality * w
                valid_weights += w
            w = 0.4
            weighted_sum += tool_success * w
            valid_weights += w
            if factors.source_count > 1:
                w = 0.2
                weighted_sum += source_agreement * w
                valid_weights += w
            if llm_self_eval > 0.6:
                w = 0.3
                weighted_sum += llm_self_eval * w
                valid_weights += w
            if query_coverage > 0.1:
                w = 0.1
                weighted_sum += query_coverage * w
                valid_weights += w
            base_score = weighted_sum / valid_weights if valid_weights > 0 else 0.0

        final_score, penalties = self._apply_penalties(base_score, factors)
        final_score = round(min(1.0, max(0.0, final_score)), 3)

        return ConfidenceScore(
            score=final_score,
            factors=factors,
            breakdown=breakdown,
            penalties_applied=penalties
        )

    def llm_calculate(
            self,
            factors: ConfidenceFactors,
            step_description: str = "",
            tool_output: str = ""
    ) -> ConfidenceScore:
        """LLMを使用した信頼度計算

        評価は統計 Factors の要約タスク（score＋短い理由の定型出力）のため、
        軽量モデル（config.llm.light_model）で実行しコストを抑える。
        """
        evaluator = create_llm_evaluator(
            config=self.config,
            model_name=self.config.llm.light_model,
        )
        eval_result = evaluator.evaluate_with_factors(
            description=step_description,
            output=tool_output,
            factors=factors
        )

        final_score = eval_result["score"]
        reason = eval_result["reason"]

        # 検索ステップで検索スコアが高い場合は検索スコアを优先（LLMが下げ過ぎるのを防ぐ）
        if factors.is_search_step and factors.search_max_score > 0.7:
            if factors.search_max_score > final_score:
                logger.info(
                    f"Override LLM score ({final_score:.4f}) with Search Score ({factors.search_max_score:.4f})")
                final_score = factors.search_max_score
                reason += f" (検索スコア {factors.search_max_score:.4f} を優先)"

        breakdown = {
            "llm_score": final_score,
            "reason": 1.0 if reason else 0.0
        }
        logger.info(f"LLM Confidence Calculation: score={final_score}, reason={reason}")

        return ConfidenceScore(
            score=final_score,
            factors=factors,
            breakdown=breakdown,
            reason=reason,
            penalties_applied=[]
        )

    def _calc_search_quality(self, factors: ConfidenceFactors) -> float:
        """RAG検索品質のスコア化"""
        if factors.search_result_count == 0 and factors.search_max_score == 0:
            return 0.0
        if factors.search_max_score >= 0.6:
            return factors.search_max_score
        combined_score = (factors.search_max_score * 0.7) + (factors.search_avg_score * 0.3)
        variance_penalty = min(0.15, factors.search_score_variance * 0.3)
        return max(0.0, combined_score - variance_penalty)

    def _calc_tool_success(self, factors: ConfidenceFactors) -> float:
        """ツール成功率の計算"""
        if factors.tool_execution_count == 0:
            return factors.tool_success_rate
        return factors.tool_success_count / factors.tool_execution_count

    def _apply_penalties(
            self,
            base_score: float,
            factors: ConfidenceFactors
    ) -> tuple[float, List[str]]:
        """特定条件でのペナルティ適用"""
        score = base_score
        penalties = []
        if factors.is_search_step and factors.search_result_count == 0:
            score *= 0.5
            penalties.append("no_search_results")
        if factors.tool_success_rate < 1.0:
            multiplier = 0.8 + 0.2 * factors.tool_success_rate
            score *= multiplier
            penalties.append(f"tool_failures(rate={factors.tool_success_rate:.2f})")
        if factors.source_count == 0:
            if factors.is_search_step and factors.search_result_count > 0:
                pass
            elif not factors.is_search_step and factors.llm_self_confidence >= 0.8:
                pass
            else:
                score *= 0.7
                penalties.append("no_sources")
        return score, penalties

    def decide_action(self, score: ConfidenceScore) -> ActionDecision:
        """信頼度スコアに基づいてアクションを決定"""
        thresholds = self.config.confidence.thresholds

        if score.score >= thresholds.silent:
            return ActionDecision(
                level=InterventionLevel.SILENT,
                confidence_score=score.score,
                reason="高い信頼度: 自動進行",
                suggested_action="proceed"
            )
        elif score.score >= thresholds.notify:
            return ActionDecision(
                level=InterventionLevel.NOTIFY,
                confidence_score=score.score,
                reason="中程度の信頼度: ステータス表示しながら進行",
                suggested_action="proceed_with_status"
            )
        elif score.score >= thresholds.confirm:
            return ActionDecision(
                level=InterventionLevel.CONFIRM,
                confidence_score=score.score,
                reason="低い信頼度: ユーザー確認を推奨",
                suggested_action="ask_confirmation"
            )
        else:
            return ActionDecision(
                level=InterventionLevel.ESCALATE,
                confidence_score=score.score,
                reason="非常に低い信頼度: 追加情報が必要",
                suggested_action="request_clarification"
            )


# =============================================================================
# LLM Self Evaluator
# =============================================================================

class FinalEvaluationResult(BaseModel):
    """最終回答の統合評価スキーマ（自己評価＋クエリ網羅度を1回の呼び出しで取得）"""
    self_eval_score: float = Field(..., ge=0.0, le=1.0, description="回答の確信度（正確性・適切性・スタイル）")
    coverage_score: float = Field(..., ge=0.0, le=1.0, description="質問要素に対する回答の網羅度")
    reason: str = Field("", description="評価理由の要約")


class LLMSelfEvaluator:
    """LLMによる自己評価"""

    FINAL_EVAL_PROMPT = """以下の【質問】に対する【回答】を2つの観点で評価し、JSON形式で出力してください。

【観点1: 確信度 (self_eval_score)】
- 正確性: 回答は提供された情報源に基づいているか？捏造はないか？
- 適切性: 質問に直接的かつ明確に答えているか？
- スタイル: 丁寧で読みやすい日本語（です・ます調）か？
スコア目安: 1.0=完全に正確・適切 / 0.6=やや確信あり / 0.4=不確実 / 0.0=不適切

【観点2: 網羅度 (coverage_score)】
- 質問のすべての要素をカバーしているか？
スコア目安: 1.0=すべての要素に回答 / 0.6=主要な要素に回答 / 0.2=ほとんど回答できていない

質問: {query}
回答: {answer}
使用した情報源: {sources}
"""

    EVAL_PROMPT = """以下の基準に基づいて、回答の確信度を0.0から1.0の数値で評価してください。

【評価基準】
1. 正確性 (Accuracy):
   - 回答は提供された情報源（検索結果）に基づいているか？
   - 情報源にない情報を捏造していないか？
2. 適切性 (Relevance):
   - ユーザーの質問に直接的かつ明確に答えているか？
   - 質問の意図を正しく理解しているか？
3. スタイル (Style):
   - 親しみやすく、丁寧な日本語（です・ます調）か？
   - 読みやすい構成か？

【スコアの目安】
- 1.0: 完全に正確で、適切かつスタイルも完璧（複数の信頼できる情報源で確認済み）
- 0.8: ほぼ確実（信頼できる情報源あり、回答も適切）
- 0.6: やや確信あり（関連情報はあるが、完全ではない、またはスタイルに改善の余地あり）
- 0.4: 不確実（情報が限定的、または質問への回答として不十分）
- 0.2: 推測に近い（根拠が弱い）
- 0.0: 全く分からない、または不適切な回答

質問: {query}
回答: {answer}
使用した情報源: {sources}

確信度（0.0-1.0の数値のみ回答）:"""

    def __init__(
            self,
            config: Optional[GraceConfig] = None,
            model_name: Optional[str] = None
    ):
        self.config = config or get_config()
        self.model_name = model_name or self.config.llm.model
        self.client = create_chat_client(self.config)
        logger.info(f"LLMSelfEvaluator initialized with model: {self.model_name}")

    def evaluate(
            self,
            query: str,
            answer: str,
            sources: Optional[List[str]] = None
    ) -> float:
        """
        LLMに自己評価させる
        Returns:
            float: 信頼度 (0.0-1.0)
        """
        sources_str = ", ".join(sources) if sources else "なし"
        prompt = self.EVAL_PROMPT.format(query=query, answer=answer, sources=sources_str)

        try:
            logger.info(f"\n{'=' * 20} [GRACE SELF-EVAL IPO: INPUT] {'=' * 20}\n{prompt}\n{'=' * 60}")

            import time as _time
            t0 = _time.time()
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={
                    "temperature": 0.0,
                    "max_output_tokens": 512,  # 出力枠が小さいと thinking/推論系モデルで本文が空になる（anthropic基準=512）
                }
            )
            elapsed = _time.time() - t0
            logger.info(f"[API時間] LLMSelfEvaluator.evaluate: {elapsed:.1f}秒")

            if response is None or response.text is None:
                logger.warning("LLM self-evaluation returned empty response")
                return 0.5

            text = response.text.strip()
            logger.info(f"\n{'=' * 20} [GRACE SELF-EVAL IPO: OUTPUT] {'=' * 20}\n{text}\n{'=' * 60}")

            confidence = float(text)
            result = min(1.0, max(0.0, confidence))
            logger.debug(f"LLM self-evaluation: {result}")
            return result

        except (ValueError, AttributeError) as e:
            logger.warning(f"Failed to parse LLM self-evaluation: {e}")
            return 0.5
        except Exception as e:
            logger.error(f"LLM self-evaluation error: {e}")
            return 0.5

    def evaluate_final(
            self,
            query: str,
            answer: str,
            sources: Optional[List[str]] = None
    ) -> FinalEvaluationResult:
        """
        最終回答の統合評価（自己評価＋クエリ網羅度）を1回のLLM呼び出しで実行

        旧実装では evaluate()（確信度）と QueryCoverageCalculator.calculate()
        （網羅度）の2回のLLM呼び出しが必要だったものを統合。

        Args:
            query: 元の質問
            answer: 生成された回答
            sources: 使用した情報源のリスト
        Returns:
            FinalEvaluationResult: 統合評価結果
        Raises:
            Exception: LLM呼び出し失敗時（呼び出し元でフォールバック処理）
        """
        sources_str = ", ".join(sources) if sources else "なし"
        prompt = self.FINAL_EVAL_PROMPT.format(query=query, answer=answer, sources=sources_str)

        import time as _time
        t0 = _time.time()
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": FinalEvaluationResult,
                "temperature": 0.0,
                "max_output_tokens": 1024,  # 出力枠が小さいと thinking/推論系モデルで本文が空になる（anthropic基準=1024）
            }
        )
        elapsed = _time.time() - t0

        if response is None or not response.text:
            raise ValueError("evaluate_final returned empty response")

        result = FinalEvaluationResult.model_validate_json(response.text)
        logger.info(
            f"[API時間] LLMSelfEvaluator.evaluate_final: {elapsed:.1f}秒 "
            f"(self_eval={result.self_eval_score:.2f}, coverage={result.coverage_score:.2f})"
        )
        return result

    def evaluate_with_factors(
            self,
            description: str,
            output: str,
            factors: ConfidenceFactors
    ) -> Dict[str, Any]:
        """
        Factorsとコンテキストを考慮した総合評価
        Returns:
            Dict: {"score": float, "reason": str}
        """

        prompt = f"""
あなたはAIエージェントの実行監視役です。
現在のステップが「成功」し、十分な信頼度があるかを評価してください。

【ステップの目的】
{description}

【実行結果（ツールの出力）】
{output[:2000]}... (省略)

【統計データ（Factors）】
- 検索品質 (Search Quality):
    - ヒット数: {factors.search_result_count}
    - 最高スコア: {factors.search_max_score:.4f}
    - 平均スコア: {factors.search_avg_score:.4f}
- ツール成功 (Tool Success):
    - 成功: {"Yes" if factors.tool_success_rate > 0.9 else "No (" + str(factors.tool_success_rate) + ")"}
- ソース一致度 (Source Agreement):
    - スコア: {factors.source_agreement:.4f} (1.0に近いほど複数の情報源が一致)
    - ソース数: {factors.source_count}

【評価基準】
以下の4項目を総合的に判断して、0.0 〜 1.0 の信頼度スコアを付けてください。

1. 検索品質: 質問に対する回答の根拠となる情報が十分にマッチしているか。
2. ツール成功: 計画されたアクションがエラーなく、期待される情報を返しているか。
3. ソース一致度: 複数の情報源がある場合、それらが矛盾していないか。
4. 目標達成度: このステップの出力だけで（またはこれまでの蓄積で）ステップの目的を達成できているか。

【スコアリング目安】
- 1.0: 完璧。根拠が明確で、矛盾もなく、目的を完全に達成した。
- 0.8: ほぼ十分。主要な情報は得られており、信頼できる。
- 0.5: 部分的。核心的な情報が不足している、または情報源に不安がある。
- 0.3: 不十分。再検索や再試行（Replan）が必要なレベル。
- 0.0: 失敗。全く無関係な情報、またはエラー。

回答は以下のJSON形式のみで出力してください。Markdownのコードブロックは不要です。
{{"score": 0.0, "reason": "評価理由"}}
"""
        try:
            logger.info(f"LLM evaluate_with_factors prompt len: {len(prompt)}")

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={
                    "temperature": 0.0,
                    "max_output_tokens": 1024,  # 構造化出力に十分な枠を確保（anthropic基準=1024）
                    # response_schema は使わず response_mime_type のみ指定する
                    "response_mime_type": "application/json",
                }
            )

            if not response or not response.text:
                logger.warning("evaluate_with_factors: empty response from LLM")
                if factors.search_max_score > 0:
                    logger.info(f"Fallback to search_max_score: {factors.search_max_score:.4f}")
                    return {"score": factors.search_max_score, "reason": "LLM empty response, using search score"}
                return {"score": 0.5, "reason": "No response from LLM"}

            import json as _json
            raw_text = response.text.strip()
            logger.info(f"evaluate_with_factors raw response ({len(raw_text)} chars): {raw_text[:300]}")

            try:
                # Step 1: response.parsed を試行
                result = getattr(response, 'parsed', None)
                if result is not None and hasattr(result, 'score') and result.score is not None:
                    score = float(result.score)
                    reason = result.reason or "No reason provided"
                    logger.info(f"evaluate_with_factors: parsed successfully: score={score}, reason={reason}")
                    return {"score": score, "reason": reason}

                # Step 2: response.text から直接JSONパース
                text = raw_text
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                json_start = text.find("{")
                json_end = text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    text = text[json_start:json_end]

                data = _json.loads(text)
                score = float(data.get("score", 0.5))
                reason = data.get("reason", "Parsed from raw text")
                logger.info(f"evaluate_with_factors: manual parse success: score={score}, reason={reason}")
                return {"score": score, "reason": reason}

            except (_json.JSONDecodeError, ValueError, KeyError, TypeError) as parse_err:
                logger.warning(f"evaluate_with_factors: all parse attempts failed: {parse_err}")
                if factors.search_max_score > 0:
                    logger.info(f"Fallback to search_max_score: {factors.search_max_score:.4f}")
                    return {"score": factors.search_max_score, "reason": "LLM parse failed, using search score"}
                return {"score": 0.5, "reason": f"Parse error: {str(parse_err)}"}

        except Exception as e:
            logger.error(f"evaluate_with_factors failed: {e}")
            if factors.search_max_score > 0:
                logger.info(f"Fallback to search_max_score: {factors.search_max_score:.4f}")
                return {"score": factors.search_max_score, "reason": "LLM evaluation failed, using search score"}
            return {"score": 0.5, "reason": f"Evaluation error: {str(e)}"}


# =============================================================================
# Source Agreement Calculator
# =============================================================================

class SourceAgreementCalculator:
    """複数ソース間の一致度計算"""

    def __init__(self, config: Optional[GraceConfig] = None):
        self.config = config or get_config()
        self.client = genai.Client()
        self.embed_model = self.config.embedding.model
        logger.info("SourceAgreementCalculator initialized")

    def calculate(self, answers: List[str]) -> float:
        """複数の回答間の一致度を計算"""
        if len(answers) < 2:
            return 1.0
        try:
            embeddings = []
            for answer in answers:
                response = self.client.models.embed_content(
                    model=self.embed_model,
                    contents=answer
                )
                embeddings.append(response.embeddings[0].values)

            similarities = []
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    sim = self._cosine_similarity(embeddings[i], embeddings[j])
                    similarities.append(sim)

            agreement = sum(similarities) / len(similarities)
            logger.debug(f"Source agreement: {agreement:.3f} from {len(answers)} sources")
            return agreement
        except Exception as e:
            logger.error(f"Source agreement calculation error: {e}")
            return 0.5

    @staticmethod
    def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot_product / (norm1 * norm2)


# =============================================================================
# Query Coverage Calculator
# =============================================================================

class QueryCoverageCalculator:
    """クエリ網羅度計算"""

    COVERAGE_PROMPT = """以下の質問に対する回答が、質問のすべての要素をカバーしているか評価してください。

質問: {query}
回答: {answer}

網羅度（0.0-1.0の数値のみ回答）:
- 1.0: すべての質問要素に完全に回答
- 0.8: ほぼすべての要素に回答
- 0.6: 主要な要素に回答
- 0.4: 一部の要素のみに回答
- 0.2: ほとんど回答できていない
- 0.0: 全く回答できていない

数値のみ回答:"""

    def __init__(
            self,
            config: Optional[GraceConfig] = None,
            model_name: Optional[str] = None
    ):
        self.config = config or get_config()
        self.model_name = model_name or self.config.llm.model
        self.client = create_chat_client(self.config)
        logger.info("QueryCoverageCalculator initialized")

    def calculate(self, query: str, answer: str) -> float:
        """クエリに対する回答の網羅度を計算"""
        prompt = self.COVERAGE_PROMPT.format(query=query, answer=answer)

        try:
            import time as _time
            t0 = _time.time()
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={
                    "temperature": 0.0,
                    "max_output_tokens": 512,  # 出力枠が小さいと thinking/推論系モデルで本文が空になる（anthropic基準=512）
                }
            )
            elapsed = _time.time() - t0
            logger.info(f"[API時間] QueryCoverageCalculator: {elapsed:.1f}秒")

            if response is None or response.text is None:
                logger.warning("QueryCoverageCalculator: empty response")
                return 0.5

            text = response.text.strip()
            logger.info(f"QueryCoverageCalculator raw response: '{text}'")
            coverage = float(text)
            result = min(1.0, max(0.0, coverage))

            # 回答が存在するのに 0.0 は異常値 → floor 0.4 を適用
            if result == 0.0 and answer and len(answer.strip()) > 20:
                logger.warning(
                    f"QueryCoverageCalculator: suspicious 0.0 for non-empty answer "
                    f"(len={len(answer)}), applying floor 0.4"
                )
                result = 0.4

            logger.debug(f"Query coverage: {result}")
            return result

        except (ValueError, AttributeError) as e:
            logger.warning(f"Failed to parse query coverage: {e}")
            return 0.5
        except Exception as e:
            logger.error(f"Query coverage calculation error: {e}")
            return 0.5


# =============================================================================
# Groundedness Verifier （S1: 根拠妥当性検証）
# =============================================================================

class ClaimVerdict(BaseModel):
    """1主張あたりの根拠判定。"""
    claim: str = Field("", description="回答から抽出した主張（短文）")
    verdict: Literal["supported", "contradicted", "neutral"] = Field(
        "neutral",
        description="引用ソースが主張を支持(supported)/矛盾(contradicted)/無関係(neutral)のいずれか",
    )


class GroundednessResponse(BaseModel):
    """groundedness 検証のLLM応答スキーマ。"""
    claims: List[ClaimVerdict] = Field(default_factory=list)
    reason: str = Field("", description="判定理由の要約")


@dataclass
class GroundednessResult:
    """groundedness 検証の集計結果。"""
    support_rate: float          # supported / 判定対象主張数（0-1）
    supported: int
    contradicted: int
    total: int                   # supported + contradicted + neutral
    has_contradiction: bool
    verified: bool               # ソースがあり検証を実施できたか
    reason: str = ""


class GroundednessVerifier:
    """最終回答の各主張が引用ソースに支持されるか（entailment）をLLM判定する。

    S1 の中核。支持率(support_rate)を信頼度の主成分に用いることで、
    「検索スコアの言い換え」だった confidence を根拠妥当性ベースへ移行する。
    """

    PROMPT = """あなたは厳密なファクトチェッカーです。
【回答】を短い主張（claim）に分解し、各主張が【情報源】によって
支持されるか判定してください。判定は次の3値のみです。

- supported   : 情報源の記述から主張が読み取れる（含意される）
- contradicted: 情報源と主張が矛盾する
- neutral     : 情報源に関連記述がなく判断できない

あなた自身の事前知識は使わず、提示された【情報源】のみを根拠にしてください。

情報源は FAQ・Q&A 形式（「Q: 質問文 / A: 回答文」等）のことがあります。
その場合は A（回答）部分の記述を通常の本文と同様に根拠として扱い、
主張が読み取れれば supported と判定してください。情報源が Q&A 形式で
あること自体を neutral（判断できない）の理由にしないでください。

# 質問
{query}

# 回答
{answer}

# 情報源
{sources}
"""

    def __init__(self, config: Optional[GraceConfig] = None,
                 model_name: Optional[str] = None):
        self.config = config or get_config()
        # M-1: claim 分解と支持判定は論理層。heavy_model 未設定なら llm.model と同じ。
        self.model_name = model_name or resolve_heavy_model(self.config)
        self.client = create_chat_client(self.config)
        logger.info(f"GroundednessVerifier initialized with model: {self.model_name}")

    def verify(self, query: str, answer: str,
               sources: Optional[List[str]] = None) -> GroundednessResult:
        """根拠妥当性を検証する。ソースが無い／LLM失敗時は verified=False を返す。"""
        if not answer or not answer.strip():
            return GroundednessResult(0.0, 0, 0, 0, False, False, "empty answer")
        if not sources:
            # 引用ソースが無い回答は検証不能（未検証）
            return GroundednessResult(0.0, 0, 0, 0, False, False, "no sources")

        sources_text = "\n\n".join(f"[{i + 1}] {s}" for i, s in enumerate(sources))
        prompt = self.PROMPT.format(query=query, answer=answer, sources=sources_text)

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": GroundednessResponse,
                    "temperature": 0.0,
                    "max_output_tokens": 1024,
                    # M-1: 論理層の拡張思考（heavy_model 設定時のみ有効。既定 0=無効）
                    "thinking_budget_tokens": heavy_thinking_budget(self.config),
                },
            )
            if not response or not response.text:
                return GroundednessResult(0.0, 0, 0, 0, False, False, "empty response")

            parsed = GroundednessResponse.model_validate_json(response.text)
            supported = sum(1 for c in parsed.claims if c.verdict == "supported")
            contradicted = sum(1 for c in parsed.claims if c.verdict == "contradicted")
            total = len(parsed.claims)
            # 判定対象（supported + contradicted）に対する支持率。
            # neutral は「根拠なし」として支持率の分母には含めるが分子には入れない。
            decided = supported + contradicted
            support_rate = (supported / decided) if decided > 0 else 0.0
            return GroundednessResult(
                support_rate=round(support_rate, 4),
                supported=supported,
                contradicted=contradicted,
                total=total,
                has_contradiction=contradicted > 0,
                verified=total > 0,
                reason=parsed.reason or "",
            )
        except Exception as e:  # 検証失敗は評価を止めない（未検証扱い）
            logger.warning(f"Groundedness verification failed: {e}")
            return GroundednessResult(0.0, 0, 0, 0, False, False, f"error: {e}")


# =============================================================================
# Confidence Aggregator
# =============================================================================

class ConfidenceAggregator:
    """複数ステップの信頼度を集計"""

    def __init__(self, config: Optional[GraceConfig] = None):
        self.config = config or get_config()
        logger.info("ConfidenceAggregator initialized")

    def aggregate(
            self,
            scores: List[ConfidenceScore],
            method: Literal["mean", "min", "weighted"] = "mean"
    ) -> float:
        """複数の信頼度スコアを集計"""
        if not scores:
            return 0.0
        values = [s.score for s in scores]
        if method == "mean":
            return sum(values) / len(values)
        elif method == "min":
            return min(values)
        elif method == "weighted":
            weights = [i + 1 for i in range(len(values))]
            total_weight = sum(weights)
            weighted_sum = sum(v * w for v, w in zip(values, weights))
            return weighted_sum / total_weight
        else:
            raise ValueError(f"Unknown aggregation method: {method}")

    def aggregate_with_critical_check(
            self,
            scores: List[ConfidenceScore],
            critical_threshold: float = 0.3
    ) -> tuple[float, bool]:
        if not scores:
            return 0.0, False
        values = [s.score for s in scores]
        has_critical_failure = any(v < critical_threshold for v in values)
        base_score = sum(values) / len(values)
        if has_critical_failure:
            return base_score * 0.7, True
        return base_score, False


# =============================================================================
# ファクトリ関数
# =============================================================================

def create_confidence_calculator(config: Optional[GraceConfig] = None) -> ConfidenceCalculator:
    return ConfidenceCalculator(config=config)


def create_llm_evaluator(
        config: Optional[GraceConfig] = None,
        model_name: Optional[str] = None
) -> LLMSelfEvaluator:
    return LLMSelfEvaluator(config=config, model_name=model_name)


def create_source_agreement_calculator(config: Optional[GraceConfig] = None) -> SourceAgreementCalculator:
    return SourceAgreementCalculator(config=config)


def create_query_coverage_calculator(
        config: Optional[GraceConfig] = None,
        model_name: Optional[str] = None
) -> QueryCoverageCalculator:
    return QueryCoverageCalculator(config=config, model_name=model_name)


def create_confidence_aggregator(config: Optional[GraceConfig] = None) -> ConfidenceAggregator:
    return ConfidenceAggregator(config=config)


def create_groundedness_verifier(
        config: Optional[GraceConfig] = None,
        model_name: Optional[str] = None
) -> GroundednessVerifier:
    return GroundednessVerifier(config=config, model_name=model_name)


# =============================================================================
# エクスポート
# =============================================================================

__all__ = [
    "ConfidenceFactors",
    "ConfidenceScore",
    "ActionDecision",
    "InterventionLevel",
    "ConfidenceCalculator",
    "LLMSelfEvaluator",
    "SourceAgreementCalculator",
    "QueryCoverageCalculator",
    "ConfidenceAggregator",
    "GroundednessVerifier",
    "GroundednessResult",
    "GroundednessResponse",
    "ClaimVerdict",
    "create_confidence_calculator",
    "create_llm_evaluator",
    "create_source_agreement_calculator",
    "create_query_coverage_calculator",
    "create_confidence_aggregator",
    "create_groundedness_verifier",
]
