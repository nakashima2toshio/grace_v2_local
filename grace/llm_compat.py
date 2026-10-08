"""
GRACE LLM 互換クライアント

GRACE 本体（planner / executor / confidence / tools）は当初
google-genai の `client.models.generate_content(...)` 形式で実装されている。
本プロジェクトは **Ollama（ローカル LLM）** を LLM プロバイダーとするため、
同一の呼び出しインターフェースを保ったまま Ollama を呼び出すアダプターを提供する。

これにより、各呼び出しサイトのコードは

    response = client.models.generate_content(
        model=...,
        contents="...",
        config={"temperature": ..., "max_output_tokens": ...},
    )
    text = response.text

をそのまま維持できる（client の生成だけ `create_chat_client(config)` に置き換える）。

Embedding（`client.models.embed_content`）は **Gemini を継続利用する**ため、
本アダプターは LLM テキスト生成（generate_content）のみを対象とする。
Qdrant のコレクションは 3072 次元のまま変わらない。

プロバイダー解決:
    - "ollama"（既定）  → OllamaGenaiClient（helper_llm.OllamaClient をラップ）
    - "gemini"/"google" → google-genai の genai.Client()
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from config import get_default_ollama_model

logger = logging.getLogger(__name__)

# 0.0〜1.0 のスコアを本文から拾うパターン。
# 小数（0.8 / .85 / 1.0）を先に試し、無ければ単独の 0 / 1 を拾う。
_SCORE_RE = re.compile(r"[01]?\.\d+|\b[01]\b")


def parse_score(text: Any) -> Optional[float]:
    """LLM 応答から 0.0〜1.0 のスコアを抽出する。

    `float(text)` の直変換は「答えは 0.8 です。」のような応答で ValueError に
    なる。ローカル LLM（Ollama）は「数値のみを出力」と指示しても前置きを付けて
    返すことがあるため、正規表現で数値部分だけを取り出す。

    Returns:
        抽出できた場合は 0.0〜1.0 にクランプした値。できなければ None
        （呼び出し側がそれぞれの既定値へフォールバックする）。
    """
    if text is None:
        return None
    match = _SCORE_RE.search(str(text).strip())
    if not match:
        return None
    try:
        value = float(match.group())
    except ValueError:  # pragma: no cover - 正規表現が保証するため到達しない
        return None
    return min(1.0, max(0.0, value))

# Gemini をそのまま使う場合のプロバイダー名（LLM 用途。embedding 検証等の限定用途）
_GEMINI_PROVIDERS = {"gemini", "google", "google-genai", "genai"}

# Ollama デフォルトモデル（config 未指定時のフォールバック）。
# 実体は config.py::get_default_ollama_model() の1箇所のみで管理する。
DEFAULT_OLLAMA_MODEL = get_default_ollama_model()


class _UsageMetadata:
    """genai の usage_metadata 互換オブジェクト。"""

    def __init__(self, prompt_token_count: int = 0, candidates_token_count: int = 0):
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count


class _GenaiCompatResponse:
    """genai の generate_content レスポンス互換オブジェクト。

    呼び出しサイトが参照する属性のみを提供する:
        - .text          : 生成テキスト
        - .parsed         : 構造化出力（常に None。呼び出し側が手動 JSON パースする）
        - .usage_metadata : トークン使用量
    """

    def __init__(self, text: str, usage: Optional[_UsageMetadata] = None):
        self.text = text
        self.parsed = None
        self.usage_metadata = usage or _UsageMetadata()


def _extract_config(config: Any) -> dict[str, Any]:
    """生成設定から必要なキーを取り出す。

    LLM テキスト生成は Ollama へ移行したため、呼び出し側は
    google-genai の `types.GenerateContentConfig` ではなく **plain dict** で
    設定を渡す。後方互換のため属性アクセス（旧 GenerateContentConfig 等）にも対応する。
    """
    if config is None:
        return {}
    out: dict[str, Any] = {}
    for key in ("temperature", "max_output_tokens", "response_mime_type",
                "response_schema"):
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


# thinking 系ローカルモデル（qwen3.5 等）が本文の前に出す思考ブロック。
# 閉じタグまで含めて 1 つの塊として剥がす。DOTALL で改行をまたぐ。
_THINK_BLOCK_RE = re.compile(r"<(think|thinking)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
# 閉じタグが無いまま出力枠を使い切った場合（= 本文へ到達していない）。
_THINK_OPEN_RE = re.compile(r"<(think|thinking)\b[^>]*>", re.IGNORECASE)


def _strip_think(text: str) -> str:
    """thinking 系モデルの `<think>…</think>` を取り除いて本文だけを返す。

    ## なぜ必要か

    Ollama には Anthropic の拡張思考に相当する API 機能は無いが、**モデルが
    自前で思考タグを出す**ことがある（qwen3.5 系が代表）。GRACE の呼び出し
    サイトは `response.text` をそのまま「回答」「JSON」「数値」として扱うため、
    思考が混ざると

      - `parse_score()` が思考中の数字を拾う
      - `json.loads()` が失敗して replan ループへ落ちる
      - 回答欄に思考がそのまま出る

    という壊れ方をする。ここで 1 回だけ剥がし、呼び出しサイトを無変更で守る。

    ⚠️ 閉じタグが無い場合（＝出力枠を思考で使い切って本文へ到達しなかった）は
    **空文字を返す**。中途半端な思考を回答として扱うより、空応答として
    呼び出し側のフォールバックへ渡すほうが安全なため。
    """
    if not text or "<think" not in text.lower():
        return text
    stripped = _THINK_BLOCK_RE.sub("", text)
    # 閉じられていない思考タグが残っていたら、そこから先は本文ではない
    open_match = _THINK_OPEN_RE.search(stripped)
    if open_match:
        logger.warning(
            "Ollama 応答が思考タグを閉じないまま終了しました"
            "（max_output_tokens が思考に足りていない可能性）。本文なしとして扱います。"
        )
        stripped = stripped[: open_match.start()]
    return stripped.strip()


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


class _OllamaModels:
    """genai の `client.models` 互換ラッパー（generate_content のみ）。"""

    def __init__(self, client_getter: Any, default_model: str):
        # client_getter は呼び出し時に helper_llm.OllamaClient を遅延生成する
        # callable。（genai.Client() と同様、構築時には SDK / 接続を要求しない）
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

        # genai の max_output_tokens を OllamaClient の max_tokens へ流用する。
        # （OllamaClient 側でも吸収するが、既定値をここで確保しておく）
        max_tokens = int(cfg.get("max_output_tokens") or 4096)
        temperature = cfg.get("temperature")

        kwargs: dict[str, Any] = {
            "model": model_name,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if temperature is not None:
            kwargs["temperature"] = float(temperature)
        # JSON モード時は Ollama の OpenAI 互換 response_format を有効化する
        if want_json:
            kwargs["response_format"] = {"type": "json_object"}

        # OllamaClient.generate_content(prompt, model=..., **kwargs) は str を返す
        text = self._get_client().generate_content(prompt, **kwargs) or ""

        # ⚠️ 思考タグの除去は **JSON 抽出より先**。<think> の中に波括弧や
        #    サンプル JSON が入っていると _strip_to_json がそちらを拾うため。
        text = _strip_think(text)

        # JSON モード時は呼び出し側が response.text を直接 model_validate_json /
        # json.loads するため、コードフェンスや前後の散文を除去する。
        if want_json and text:
            text = _strip_to_json(text)

        # ローカル実行のためコストは常に 0。usage は互換のため空で返す。
        return _GenaiCompatResponse(text=text, usage=_UsageMetadata())


class OllamaGenaiClient:
    """genai.Client 互換の Ollama クライアント。

    `.models.generate_content(...)` のみをサポートする。
    内部で helper.helper_llm.OllamaClient を遅延生成して使用する。
    """

    def __init__(
        self,
        default_model: str,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self._default_model = default_model
        self._base_url = base_url
        # ⚠️ ローカル LLM の 1 リクエスト期限（秒）。None なら helper_llm の既定。
        #    ここを通さないと openai SDK の既定 600 秒 × 3 回が効いてしまう。
        self._timeout = timeout
        self._client: Any = None
        # genai.Client() と同様、構築時には接続を行わず、最初の
        # generate_content 呼び出し時に遅延生成する（import 安全性のため）。
        self.models = _OllamaModels(self._ensure_client, default_model)

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from helper.helper_llm import create_llm_client
            except ImportError:  # pragma: no cover - フラット配置フォールバック
                from helper_llm import create_llm_client  # type: ignore[no-redef]
            kwargs: dict[str, Any] = {"default_model": self._default_model}
            # base_url 未指定なら helper_llm 側が OLLAMA_BASE_URL → 既定値で解決する
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._timeout:
                kwargs["timeout"] = self._timeout
            self._client = create_llm_client("ollama", **kwargs)
        return self._client


def create_chat_client(config: Any = None) -> Any:
    """GRACE 本体のテキスト生成用クライアントを生成する。

    config.llm.provider に応じて以下を返す:
        - "ollama"（既定）  → OllamaGenaiClient（genai 互換）
        - "gemini"/"google" → google-genai の genai.Client()

    いずれの戻り値も `client.models.generate_content(...)` を提供する。
    """
    provider = "ollama"
    model = None
    timeout = None
    llm = getattr(config, "llm", None) if config is not None else None
    if llm is not None:
        # 検証するのは文字列で指定された名前だけ（テストの MagicMock 等、文字列でない値は既定のまま）
        configured = getattr(llm, "provider", None)
        if isinstance(configured, str) and configured:
            provider = configured.lower()
        model = getattr(llm, "model", None) or None
        # config.llm.timeout（grace_config.yml の llm.timeout）を実際に効かせる。
        # ここで渡さないと openai SDK の既定 600 秒 × 3 回になり、1 呼び出しが
        # 最大 30 分ブロックする。
        timeout = getattr(llm, "timeout", None) or None

    if provider in _GEMINI_PROVIDERS:
        from google import genai
        return genai.Client()

    # ⚠️ 未知のプロバイダ名は ValueError。2026-09-26 まで黙って Ollama にしていたため、
    #    grace_config.yml の llm.provider の打ち間違い（例 "olama"）に気付けなかった。
    if provider != "ollama":
        raise ValueError(
            f"未知の LLM プロバイダです: config.llm.provider={provider!r}"
            "（ollama / gemini のいずれか）"
        )

    # config.ollama.base_url があれば使う（無ければ helper_llm が環境変数で解決）
    ollama_cfg = getattr(config, "ollama", None) if config is not None else None
    base_url = getattr(ollama_cfg, "base_url", None) if ollama_cfg is not None else None

    return OllamaGenaiClient(
        default_model=model or DEFAULT_OLLAMA_MODEL,
        base_url=base_url,
        timeout=timeout,
    )
