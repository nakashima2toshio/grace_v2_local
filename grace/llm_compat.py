"""
GRACE LLM 互換クライアント

GRACE 本体（planner / executor / confidence / tools）は当初
google-genai の `client.models.generate_content(...)` 形式で実装されている。
本プロジェクトは Anthropic を LLM プロバイダーとするため、同一の呼び出し
インターフェースを保ったまま Anthropic API を呼び出すアダプターを提供する。

これにより、各呼び出しサイトのコードは

    response = client.models.generate_content(
        model=...,
        contents="...",
        config=types.GenerateContentConfig(...),
    )
    text = response.text

をそのまま維持できる（client の生成だけ `create_chat_client(config)` に置き換える）。

Embedding（`client.models.embed_content`）は Gemini を継続利用するため、
本アダプターは LLM テキスト生成（generate_content）のみを対象とする。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Gemini をそのまま使う場合のプロバイダー名
_GEMINI_PROVIDERS = {"gemini", "google", "google-genai", "genai"}

# Anthropic デフォルトモデル（config 未指定時のフォールバック）
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"

# 拡張思考を有効にするときに本文用として最低限確保するトークン数。
# Anthropic は max_tokens > budget_tokens を要求し、差分が本文の取り分になる。
_MIN_TEXT_TOKENS = 1024

# Anthropic が要求する thinking budget の下限。
_MIN_THINKING_BUDGET = 1024


def _thinking_budget(requested: Any, max_tokens: int) -> int:
    """拡張思考の budget を正規化する。0 / None / 不正値は「無効」。

    呼び出し側が `max_output_tokens` しか意識していないケースを壊さないよう、
    budget を要求されたときはここで下限（API 要件）を満たすまで引き上げる。
    max_tokens 側は呼び出し元で `budget + _MIN_TEXT_TOKENS` まで広げる。
    """
    try:
        value = int(requested or 0)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    return max(value, _MIN_THINKING_BUDGET)


class _UsageMetadata:
    """genai の usage_metadata 互換オブジェクト。"""

    def __init__(self, prompt_token_count: int = 0, candidates_token_count: int = 0):
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count


class _GenaiCompatResponse:
    """genai の generate_content レスポンス互換オブジェクト。

    呼び出しサイトが参照する属性のみを提供する:
        - .text          : 生成テキスト
        - .parsed         : 構造化出力（Anthropic では None。呼び出し側が手動 JSON パースする）
        - .usage_metadata : トークン使用量
    """

    def __init__(self, text: str, usage: Optional[_UsageMetadata] = None):
        self.text = text
        self.parsed = None
        self.usage_metadata = usage or _UsageMetadata()


def _extract_config(config: Any) -> dict[str, Any]:
    """生成設定から必要なキーを取り出す。

    LLM テキスト生成は Anthropic 専用へ移行したため、呼び出し側は
    google-genai の `types.GenerateContentConfig` ではなく **plain dict** で
    設定を渡す。後方互換のため属性アクセス（旧 GenerateContentConfig 等）にも対応する。
    """
    if config is None:
        return {}
    out: dict[str, Any] = {}
    for key in ("temperature", "max_output_tokens", "response_mime_type",
                "response_schema", "thinking_budget_tokens"):
        if isinstance(config, dict):
            out[key] = config.get(key)
        else:
            out[key] = getattr(config, key, None)
    return out


def _schema_hint(response_schema: Any) -> str:
    """response_schema から JSON スキーマのヒント文字列を生成する。"""
    if response_schema is None:
        return ""
    # Pydantic モデルクラスの場合は JSON Schema を埋め込む
    model_json_schema = getattr(response_schema, "model_json_schema", None)
    if callable(model_json_schema):
        try:
            return json.dumps(model_json_schema(), ensure_ascii=False)
        except Exception:  # pragma: no cover - スキーマ生成失敗は無視
            return ""
    return ""


def _strip_to_json(text: str) -> str:
    """Markdown フェンスや前後の散文を除去し、JSON 本体（{...} or [...]）を抽出する。"""
    s = text.strip()
    if s.startswith("```"):
        # ```json ... ``` / ``` ... ``` を剥がす
        body = s.split("\n", 1)
        if len(body) == 2:
            s = body[1]
        s = s.rsplit("```", 1)[0].strip()
    # オブジェクト/配列のいずれか先に出現する方を抽出
    candidates = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not candidates:
        return s
    start = min(candidates)
    end = max(s.rfind("}"), s.rfind("]")) + 1
    if end > start:
        return s[start:end]
    return s


class _AnthropicModels:
    """genai の `client.models` 互換ラッパー（generate_content のみ）。"""

    def __init__(self, client_getter: Any, default_model: str):
        # client_getter は呼び出し時に Anthropic クライアントを遅延生成する callable。
        # （genai.Client() と同様、構築時には SDK import / API キーを要求しない）
        self._get_client = client_getter
        self._default_model = default_model

    def generate_content(
        self,
        model: Optional[str] = None,
        contents: Any = None,
        config: Any = None,
        **_kwargs: Any,
    ) -> _GenaiCompatResponse:
        cfg = _extract_config(config)
        model_name = model or self._default_model

        # contents は GRACE 本体では常に str。念のため文字列化する。
        prompt = contents if isinstance(contents, str) else str(contents)

        # JSON 出力が要求されている場合（mime or schema）はシステム指示を付与
        want_json = bool(cfg.get("response_mime_type") == "application/json"
                         or cfg.get("response_schema") is not None)

        system_parts: list[str] = []
        if want_json:
            system_parts.append(
                "あなたは厳密な JSON ジェネレーターです。"
                "出力は有効な JSON オブジェクト 1 個のみとし、"
                "Markdown のコードブロックや説明文を一切含めないでください。"
            )
            hint = _schema_hint(cfg.get("response_schema"))
            if hint:
                system_parts.append(f"出力は次の JSON Schema に厳密に従ってください:\n{hint}")
        system_prompt = "\n\n".join(system_parts) if system_parts else None

        # Anthropic は max_tokens 必須。genai の max_output_tokens を流用し、
        # 未指定時は十分な既定値を確保する。
        max_tokens = int(cfg.get("max_output_tokens") or 2048)
        temperature = cfg.get("temperature")

        kwargs: dict[str, Any] = {
            "model": model_name,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        # --- 拡張思考（extended thinking）の明示制御 ---------------------------
        # 一部のモデル（claude-opus-5 等）は thinking が **既定で有効**で、
        # その場合 max_tokens は「思考 + 本文」の合計上限になり、temperature は
        # 指定できない。GRACE の呼び出しサイトには
        # `max_output_tokens: 10`（複雑度推定・意図分類・情報なし判定）や
        # `temperature: 0.0`（groundedness / JSON 生成）が多数あるため、
        # 既定を暗黙に任せるとモデル差し替えの瞬間に「本文が空」「API エラー」で
        # 壊れる。ここで **常に明示** し、呼び出し側が budget を渡したときだけ有効化する。
        budget = _thinking_budget(cfg.get("thinking_budget_tokens"), max_tokens)
        if budget:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # 思考中は温度指定不可。呼び出し側の temperature は無視する。
            kwargs["max_tokens"] = max(max_tokens, budget + _MIN_TEXT_TOKENS)
        else:
            kwargs["thinking"] = {"type": "disabled"}
            if temperature is not None:
                kwargs["temperature"] = float(temperature)

        message = self._get_client().messages.create(**kwargs)

        # text ブロックを連結
        text_parts: list[str] = []
        for block in getattr(message, "content", []) or []:
            block_text = getattr(block, "text", None)
            if block_text:
                text_parts.append(block_text)
        text = "".join(text_parts)

        # JSON モード時は呼び出し側が response.text を直接 model_validate_json /
        # json.loads するため、Markdown コードフェンスや前後の散文を除去して
        # 純粋な JSON 本体のみを返す。
        if want_json and text:
            text = _strip_to_json(text)

        usage = getattr(message, "usage", None)
        usage_meta = _UsageMetadata(
            prompt_token_count=getattr(usage, "input_tokens", 0) or 0,
            candidates_token_count=getattr(usage, "output_tokens", 0) or 0,
        )
        return _GenaiCompatResponse(text=text, usage=usage_meta)


class AnthropicGenaiClient:
    """genai.Client 互換の Anthropic クライアント。

    `.models.generate_content(...)` のみをサポートする。
    """

    def __init__(self, default_model: str, api_key: Optional[str] = None):
        self._default_model = default_model
        self._api_key = api_key
        self._client: Any = None
        # genai.Client() と同様、構築時には SDK import / API キー検証を行わず、
        # 最初の generate_content 呼び出し時に遅延生成する（import 安全性のため）。
        self.models = _AnthropicModels(self._ensure_client, default_model)

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - 依存未導入
                raise ImportError(
                    "anthropic パッケージが必要です。`pip install anthropic` を実行してください。"
                ) from exc
            # API キー・ベース URL は環境変数（ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL）から解決
            self._client = (
                anthropic.Anthropic(api_key=self._api_key)
                if self._api_key else anthropic.Anthropic()
            )
        return self._client


def create_chat_client(config: Any = None) -> Any:
    """GRACE 本体のテキスト生成用クライアントを生成する。

    config.llm.provider に応じて以下を返す:
        - "gemini"/"google" → google-genai の genai.Client()
        - "anthropic"       → AnthropicGenaiClient（genai 互換）

    いずれの戻り値も `client.models.generate_content(...)` を提供する。
    """
    provider = "anthropic"
    model = DEFAULT_ANTHROPIC_MODEL
    llm = getattr(config, "llm", None) if config is not None else None
    if llm is not None:
        provider = (getattr(llm, "provider", None) or provider).lower()
        model = getattr(llm, "model", None) or model

    if provider in _GEMINI_PROVIDERS:
        from google import genai
        return genai.Client()

    return AnthropicGenaiClient(default_model=model)
