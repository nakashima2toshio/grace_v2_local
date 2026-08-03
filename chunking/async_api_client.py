# async_api_client.py
"""
非同期APIクライアント（チャンク化用・構造化出力）

[MIGRATION gemini→anthropic]
  google.genai の models.generate_content(response_schema=...) ベースから、
  Anthropic（create_llm_client("anthropic").generate_structured）ベースへ移行。
  同期 API を asyncio.to_thread() でラップし、Semaphore で並列数を制御する。
  - 戻り値契約は従来どおり「検証済み JSON 文字列」（呼び出し側が
    model_validate_json() でパースする）を維持。
  - LLM は Anthropic Claude（既定 claude-sonnet-4-6）。
"""

import asyncio
import logging
from typing import Optional, Type

from pydantic import BaseModel

from helper.helper_llm import create_llm_client

logger = logging.getLogger(__name__)


class AsyncAPIClient:
    """
    非同期APIクライアント
    - asyncio.to_thread() で同期APIをラップ
    - Semaphore で並列数制御（固定）
    - リトライロジック（3回、指数バックオフ）
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        max_workers: int = 8,
        max_retries: int = 3,
        max_output_tokens: int = 8192,
        default_model: str = "claude-sonnet-4-6",
    ):
        """
        Args:
            api_key: 後方互換のため残置（未使用。Anthropic は ANTHROPIC_API_KEY を参照）
            max_workers: 並列数（デフォルト: 8、固定）
            max_retries: リトライ回数（デフォルト: 3）
            max_output_tokens: 出力トークン制限
            default_model: 既定 Claude モデル
        """
        # [MIGRATION] genai.Client → 統一 Anthropic クライアント
        self.llm = create_llm_client("anthropic", default_model=default_model)
        self.default_model = default_model
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens
        self._total_requests = 0
        self._failed_requests = 0
        self._truncated_responses = 0

    @staticmethod
    def _resolve_model(model: Optional[str], default_model: str) -> str:
        """渡されたモデル名が Claude 系でなければ既定 Claude モデルへ回避する。

        チャンク化呼び出し側はレガシーで Gemini モデル名を渡す場合があるため、
        Anthropic エンドポイントに非 Claude 名を投げて失敗しないよう保護する。
        """
        if model and str(model).lower().startswith("claude"):
            return model
        return default_model

    async def generate_content(
        self,
        model: str,
        contents: str,
        response_schema: Type[BaseModel],
        task_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        セマフォで並列数を制御しながら構造化出力を生成する。
        失敗時は指数バックオフでリトライ。

        Returns:
            検証済み JSON 文字列、または失敗時は None
        """
        async with self.semaphore:
            return await self._execute_with_retry(
                model, contents, response_schema, task_id
            )

    async def _execute_with_retry(
        self,
        model: str,
        contents: str,
        response_schema: Type[BaseModel],
        task_id: Optional[str],
    ) -> Optional[str]:
        """リトライロジック（レート制限・一時エラー対応）"""
        effective_model = self._resolve_model(model, self.default_model)

        for attempt in range(self.max_retries):
            try:
                self._total_requests += 1

                # [MIGRATION] 同期の generate_structured を to_thread で非同期実行。
                # response_schema 準拠の Pydantic インスタンスを取得し JSON 文字列化して返す。
                obj = await asyncio.to_thread(
                    self.llm.generate_structured,
                    contents,
                    response_schema,
                    effective_model,
                    max_output_tokens=self.max_output_tokens,
                )
                return obj.model_dump_json()

            except Exception as e:
                error_str = str(e).lower()

                # レート制限エラーの判定
                if "429" in error_str or "rate" in error_str or "quota" in error_str:
                    wait_time = 30 * (attempt + 1)
                    logger.warning(
                        f"[{task_id}] Rate limit hit. "
                        f"Waiting {wait_time}s (attempt {attempt + 1}/{self.max_retries})"
                    )
                else:
                    wait_time = 2 ** attempt
                    logger.warning(
                        f"[{task_id}] Error: {e}. "
                        f"Retrying in {wait_time}s (attempt {attempt + 1}/{self.max_retries})"
                    )

                if attempt < self.max_retries - 1:
                    await asyncio.sleep(wait_time)

        # 全リトライ失敗
        self._failed_requests += 1
        logger.error(f"[{task_id}] Failed after {self.max_retries} retries. Using fallback.")
        return None

    def get_stats(self) -> dict:
        """統計情報を取得"""
        return {
            "total_requests": self._total_requests,
            "failed_requests": self._failed_requests,
            "truncated_responses": self._truncated_responses,
            "success_rate": (
                (self._total_requests - self._failed_requests) / self._total_requests * 100
                if self._total_requests > 0 else 0
            ),
            "concurrency": self.max_workers
        }

    def reset_stats(self):
        """統計情報をリセット"""
        self._total_requests = 0
        self._failed_requests = 0
        self._truncated_responses = 0
