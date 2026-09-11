# async_api_client.py
"""
非同期APIクライアント（チャンク化用・構造化出力）

google.genai ベースから、ローカル LLM（create_llm_client("ollama").
generate_structured）ベースへ移行済み。同期 API を asyncio.to_thread() で
ラップし、Semaphore で並列数を制御する。
  - 戻り値契約は「検証済み JSON 文字列」（呼び出し側が model_validate_json()
    でパースする）。
  - LLM はローカル LLM = Ollama。既定は config.py::get_default_ollama_model()
    だが、**呼び出し側がモデルを渡したらそれを使う**（_resolve_model 参照）。
"""

import asyncio
import logging
import os
from typing import Optional, Type

from pydantic import BaseModel

from config import get_default_chunking_workers, get_default_ollama_model
from helper.helper_llm import create_llm_client

logger = logging.getLogger(__name__)

# 連続でこの回数失敗したらジョブごと中断する（0 で無効）。
#
# ⚠️ **これが無いと、失敗し続けても止まらない。** 全ブロックが
# タイムアウトしても 1 件ずつフォールバック（機械的分割）に落ちて先へ進み、
# 1229 ブロックを 185 時間かけて処理したあげく、中身のない CSV を
# 「成功」として書き出す。実際にそうなった（2026-09-06）。
DEFAULT_ABORT_AFTER_CONSECUTIVE_FAILURES = int(
    os.getenv("CHUNKING_ABORT_AFTER_FAILURES", "3")
)

# チャンク化の LLM タイムアウト（秒）。未設定なら LLM クライアントの既定に従う。
#
# ⚠️ **既定値をここで上書きしない。** 対話用の既定（helper_llm の
# OLLAMA_TIMEOUT = 180 秒）はエージェントの 1 ステップ向けで、バッチの
# チャンク化には短いことがある。ただし延ばすと失敗の検知も遅くなるため、
# 「遅いだけのモデルを使う」と分かっている人が明示的に延ばす形にする。
_CHUNKING_LLM_TIMEOUT_ENV = "CHUNKING_LLM_TIMEOUT"


class ChunkingAbortedError(RuntimeError):
    """LLM 呼び出しが連続で失敗したためチャンク化を中断した。

    呼び出し側（`_chunking_runner`）はこれを捕捉して error イベントへ変換する。
    **握りつぶしてフォールバックで続行してはいけない。** 続行しても、
    LLM を使わない機械的な分割結果しか得られない。
    """


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
        max_workers: Optional[int] = None,
        max_retries: int = 3,
        max_output_tokens: int = 8192,
        default_model: Optional[str] = None,
        abort_after_consecutive_failures: Optional[int] = None,
    ):
        """
        Args:
            api_key: 後方互換のため残置（未使用。LLM はローカル実行でキー不要）
            max_workers: 並列数。None なら get_default_chunking_workers()
                （OLLAMA_NUM_PARALLEL があればその値、無ければ 1）
            max_retries: リトライ回数（デフォルト: 3）
            max_output_tokens: 出力トークン制限
            abort_after_consecutive_failures: 連続失敗の許容回数。超えたら
                `ChunkingAbortedError` を送出してジョブを止める。None なら
                `CHUNKING_ABORT_AFTER_FAILURES`（既定 3）。0 で無効
            default_model: モデル未指定の呼び出しで使う既定。
                ⚠️ **既定値をシグネチャに書かない。** 関数シグネチャの既定は
                import 時に 1 度だけ評価されるため、`get_default_ollama_model()`
                をここに置くと `.env` の `OLLAMA_DEFAULT_MODEL` が焼き付き、
                あとから変えても効かない。None のまま受けて下で解決する。
        """
        self.default_model = (default_model or "").strip() or get_default_ollama_model()

        # タイムアウトは未設定なら渡さない（クライアント側の既定に従う）
        client_kwargs = {"default_model": self.default_model}
        timeout_env = os.getenv(_CHUNKING_LLM_TIMEOUT_ENV, "").strip()
        if timeout_env:
            try:
                client_kwargs["timeout"] = float(timeout_env)
            except ValueError:
                logger.warning(
                    "%s の値が数値ではありません: %r（無視して既定を使います）",
                    _CHUNKING_LLM_TIMEOUT_ENV,
                    timeout_env,
                )
        self.llm = create_llm_client("ollama", **client_kwargs)

        self.abort_after_consecutive_failures = (
            DEFAULT_ABORT_AFTER_CONSECUTIVE_FAILURES
            if abort_after_consecutive_failures is None
            else int(abort_after_consecutive_failures)
        )
        self._consecutive_failures = 0
        # ⚠️ **ローカル LLM では既定 1。** 上げても速くならず、待ち行列が
        #    各リクエストのタイムアウトを食うだけになる（実測の根拠は
        #    config.get_default_chunking_workers() の docstring）。
        self.max_workers = (
            get_default_chunking_workers() if max_workers is None else max(1, int(max_workers))
        )
        self.semaphore = asyncio.Semaphore(self.max_workers)
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens
        self._total_requests = 0
        self._failed_requests = 0
        self._truncated_responses = 0

    @staticmethod
    def _resolve_model(model: Optional[str], default_model: str) -> str:
        """使うモデル名を決める。**指定されたモデルは捨てない。**

        未指定（None / 空文字 / 空白）のときだけ `default_model` へ倒す。

        ## ⚠️ 以前ここにあったバグ

        Anthropic 移植時代の名残で、こういう分岐が残っていた:

        ```python
        if model and str(model).lower().startswith("claude"):
            return model
        return default_model     # ← "claude" で始まらなければ捨てる
        ```

        本リポジトリの LLM はローカル（Ollama）で、モデル名は
        `gemma4:12b-mlx` のように **"claude" では始まらない**。つまり
        **呼び出し側が渡したモデルは 100% 捨てられていた**。画面の
        モデル欄も `_resolve_model()` の解決結果も、ここで無効化されていた:

            チャンク化処理開始 (3段階)
            モデル: gemma4:12b-mlx                          ← 解決結果
            OllamaClient initialized: ... model=gemma4:e4b  ← 実際に使われた値
            [step1_block_242] Error: 404 model 'gemma4:e4b' not found

        「モデル名でプロバイダを推測して差し替える」たぐいの分岐は入れないこと。
        呼び出し側が決めたモデルを、そのまま使う。
        """
        chosen = (model or "").strip()
        return chosen or default_model

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
        last_error: Optional[Exception] = None

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
                self._consecutive_failures = 0
                return obj.model_dump_json()

            except Exception as e:
                last_error = e
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
        self._consecutive_failures += 1

        limit = self.abort_after_consecutive_failures
        if limit and self._consecutive_failures >= limit:
            # ⚠️ **ここで止める。** フォールバックで先へ進むと、LLM を一度も
            #    使えていないのに「成功」で終わる。残りブロック分の時間
            #    （実測で 1 ブロック 543 秒 × 1229 ブロック）を捨てる前に返す。
            raise ChunkingAbortedError(
                f"LLM 呼び出しが {self._consecutive_failures} 回連続で失敗したため"
                f"チャンク化を中断しました。最後のエラー: {last_error}\n"
                "   確認してください:\n"
                "   - ollama serve が動いているか / そのモデルが pull 済みか\n"
                "   - 1 ブロックの処理がタイムアウトより長くないか"
                f"（延ばす場合は {_CHUNKING_LLM_TIMEOUT_ENV}=600 のように指定）\n"
                "   - 並列ワーカー数を下げる（ローカル LLM は同時実行で遅くなる）"
            )

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
