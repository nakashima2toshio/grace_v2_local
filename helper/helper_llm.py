"""
LLMクライアント抽象化レイヤー

本プロジェクトの LLM 既定は Anthropic（Claude）。Anthropic / OpenAI / Gemini の
3プロバイダーに対応する統一インターフェースを提供する。
  - テキスト生成: generate_content()
  - 構造化出力: generate_structured()
  - Tool Use（ReAct ループ）: generate_with_tools() / build_tool_result_message()
Embedding は別モジュール（helper_embedding）が担当し、本モジュールは LLM 生成のみ。
Gemini は後方互換のため残置（google.genai は GeminiClient 内で遅延 import）。
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, NamedTuple, Optional, Type

from dotenv import load_dotenv
from pydantic import BaseModel

# SDK imports
# try:
#     from openai import OpenAI
# except ImportError:
#     OpenAI = None
#
# try:
#     from google import genai
#     from google.genai import types
# except ImportError:
#     genai = None
#     types = None

# SDK imports <-- new API
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

import tiktoken

# 注: google-genai（genai / types）は GeminiClient 専用。本プロジェクトの LLM 既定は
# Anthropic のため、google-genai を top-level import せず GeminiClient 内で遅延 import する
# （embedding は別モジュール helper_embedding が担当）。

load_dotenv()

logger = logging.getLogger(__name__)

# --- LLM モデル設定 --- #
# 本プロジェクトの LLM は Anthropic（Claude）。Gemini は後方互換のため残置。
LLM_MODELS = [
    "claude-sonnet-4-6",          # デフォルト（GRACE 本体・推論）
    "claude-haiku-4-5-20251001",  # 文字列処理・eval ジャッジ向け
    "gemini-2.5-flash",
    "gemini-2.5-flash-preview",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

# 価格は 1K トークンあたりの USD（概算）
LLM_PRICING = {
    "claude-sonnet-4-6"          : {"input": 0.003, "output": 0.015},
    "claude-haiku-4-5-20251001"  : {"input": 0.001, "output": 0.005},
    "gemini-2.5-flash"        : {"input": 0.0001, "output": 0.0004},  # Estimated
    "gemini-2.5-flash-preview": {"input": 0.00015, "output": 0.0035},
    "gemini-2.0-flash"        : {"input": 0.0001, "output": 0.0004},
    "gemini-1.5-pro"          : {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash"        : {"input": 0.000075, "output": 0.0003},
}

LLM_LIMITS = {
    "claude-sonnet-4-6"          : {"max_tokens": 200000, "max_output": 8192},
    "claude-haiku-4-5-20251001"  : {"max_tokens": 200000, "max_output": 8192},
    "gemini-2.5-flash"        : {"max_tokens": 1000000, "max_output": 8192},
    "gemini-2.5-flash-preview": {"max_tokens": 1000000, "max_output": 64000},
    "gemini-2.0-flash"        : {"max_tokens": 1000000, "max_output": 8192},
    "gemini-1.5-pro"          : {"max_tokens": 1000000, "max_output": 8192},
    "gemini-1.5-flash"        : {"max_tokens": 1000000, "max_output": 8192},
}

# --- Embedding モデル設定 --- #
EMBEDDING_MODELS = [
    "gemini-embedding-001",
    "text-embedding-3-small",
    "text-embedding-3-large",
]

EMBEDDING_PRICING = {
    "gemini-embedding-001"  : 0.0001,
    "text-embedding-3-small": 0.00002,
    "text-embedding-3-large": 0.00013,
}

EMBEDDING_DIMS = {
    "gemini-embedding-001"  : 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}

DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")


class ToolUseResponse(NamedTuple):
    """generate_with_tools() の戻り値。

    text:              LLM のテキスト応答
    tool_calls:        [{"name":..., "input":..., "id":...}, ...]
    stop_reason:       "tool_use" | "end_turn" | "stop" | "length"
    assistant_message: {"role": "assistant", "content": response.content}
                       会話履歴 (_messages) にそのまま追記できる形式
    """
    text: str
    tool_calls: List[Dict[str, Any]]
    stop_reason: str
    assistant_message: Dict[str, Any]


class LLMClient(ABC):
    @abstractmethod
    def generate_content(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        pass

    @abstractmethod
    def generate_structured(self, prompt: str, response_schema: Type[BaseModel], model: Optional[str] = None,
                            **kwargs) -> BaseModel:
        pass

    @abstractmethod
    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        pass


class OpenAIClient(LLMClient):
    def __init__(self, api_key: Optional[str] = None, default_model: str = "gpt-4o-mini"):
        if not OpenAI:
            raise ImportError("openai package is not installed.")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not set")
        self.client = OpenAI(api_key=self.api_key)
        self.default_model = default_model

    def generate_content(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        model = model or self.default_model
        messages = [{"role": "user", "content": prompt}]
        response = self.client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response.choices[0].message.content

    def generate_structured(self, prompt: str, response_schema: Type[BaseModel], model: Optional[str] = None,
                            **kwargs) -> BaseModel:
        model = model or self.default_model
        messages = [{"role": "user", "content": prompt}]
        response = self.client.beta.chat.completions.parse(
            model=model,
            messages=messages,
            response_format=response_schema,
            **kwargs
        )
        return response.choices[0].message.parsed

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        model = model or self.default_model
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))


class GeminiClient(LLMClient):
    def __init__(self, api_key: Optional[str] = None, default_model: str = "gemini-2.5-flash"):
        from google import (
            genai,  # 遅延 import（google-genai を top-level に持ち込まない）
        )
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY (or GEMINI_API_KEY) is not set")
        self.client = genai.Client(api_key=self.api_key)
        self.default_model = default_model

    def generate_content(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        from google.genai import types  # 遅延 import
        model_name = model or self.default_model

        config = {
            # AFC は常に無効化（有効のままにすると空レスポンスが発生するバグあり）
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if "temperature" in kwargs:
            config["temperature"] = kwargs.pop("temperature")
        if "max_output_tokens" in kwargs:
            config["max_output_tokens"] = kwargs.pop("max_output_tokens")

        response = self.client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(**config)
        )

        return response.text

    def generate_structured(self, prompt: str, response_schema: Type[BaseModel], model: Optional[str] = None,
                            **kwargs) -> BaseModel:
        from google.genai import types  # 遅延 import
        model_name = model or self.default_model

        # JSON スキーマの設定
        config = {
            "response_mime_type": "application/json",
            "response_schema"   : response_schema.model_json_schema()
        }

        if "temperature" in kwargs:
            config["temperature"] = kwargs.pop("temperature")
        if "max_output_tokens" in kwargs:
            config["max_output_tokens"] = kwargs.pop("max_output_tokens")

        # スキーマをプロンプトに追加
        schema_prompt = f"{prompt}\n\nOutput in JSON format following this schema: {response_schema.model_json_schema()}"

        response = self.client.models.generate_content(
            model=model_name,
            contents=schema_prompt,
            config=types.GenerateContentConfig(**config)
        )

        try:
            return response_schema.model_validate_json(response.text)
        except Exception as e:
            logger.error(f"JSON parse error: {e}")
            logger.error(f"Raw response text from Gemini:\n{response.text}")
            raise

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        model_name = model or self.default_model
        response = self.client.models.count_tokens(
            model=model_name,
            contents=text
        )
        return response.total_tokens


class AnthropicClient(LLMClient):
    """Anthropic (Claude) API クライアント。

    本プロジェクトの LLM プロバイダー。Embedding は別途 Gemini を使用するため、
    本クラスはテキスト生成・構造化出力のみを担当する。
    API キー・ベース URL は環境変数（ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL）から解決。
    """

    def __init__(self, api_key: Optional[str] = None, default_model: str = "claude-sonnet-4-6"):
        # 遅延初期化: SDK import / クライアント生成は最初の API 呼び出し時まで遅延する。
        # （GeminiClient と異なり anthropic.Anthropic() は API キー必須のため、
        #   構築だけで失敗しないよう副作用を持たせない。テスト容易性のためにも重要。）
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.default_model = default_model
        self._client = None
        # 直近の API 呼び出しのトークン使用量（per-call usage 配管）。
        # generate_content / generate_structured の呼び出しごとに更新される。
        self.last_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ImportError(
                    "anthropic package is not installed. Run `pip install anthropic`."
                ) from exc
            # ANTHROPIC_BASE_URL 等は SDK が環境変数から解決する
            self._client = (
                anthropic.Anthropic(api_key=self.api_key)
                if self.api_key else anthropic.Anthropic()
            )
        return self._client

    def _create(self, prompt: str, model: Optional[str], system: Optional[str] = None,
                **kwargs) -> str:
        model = model or self.default_model
        max_tokens = kwargs.pop("max_tokens", None) or kwargs.pop("max_output_tokens", None) or 2048
        create_kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_tokens),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            create_kwargs["system"] = system
        if "temperature" in kwargs:
            create_kwargs["temperature"] = kwargs.pop("temperature")
        message = self._get_client().messages.create(**create_kwargs)
        # per-call usage を記録（usage が無い/壊れている場合は 0）
        usage = getattr(message, "usage", None)
        self.last_usage = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
        return "".join(
            getattr(block, "text", "") or "" for block in (getattr(message, "content", []) or [])
        )

    def generate_content(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        return self._create(prompt, model, **kwargs)

    def generate_structured(self, prompt: str, response_schema: Type[BaseModel],
                            model: Optional[str] = None, **kwargs) -> BaseModel:
        schema = json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
        system = (
            "あなたは厳密な JSON ジェネレーターです。出力は有効な JSON オブジェクト 1 個のみとし、"
            "Markdown のコードブロックや説明文を含めないでください。\n"
            f"出力は次の JSON Schema に厳密に従ってください:\n{schema}"
        )
        text = self._create(prompt, model, system=system, **kwargs).strip()
        # コードフェンス除去 + JSON 本体抽出（堅牢化）
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]
        return response_schema.model_validate_json(text)

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        # tiktoken による近似（Anthropic 専用トークナイザは未使用）
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))

    def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: str = "",
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> ToolUseResponse:
        """Tool Use を含む ReAct ループの 1 ステップを実行する（Anthropic 形式）。

        Anthropic Messages API の Tool Use（input_schema 形式のツール定義）を用い、
        stop_reason=="tool_use" でツール呼び出しを検出する。tools=[] を渡すと
        ツールなしの純粋なテキスト生成（Reflection など）として動作する。
        """
        model_name = model or self.default_model

        create_kwargs: Dict[str, Any] = {
            "model": model_name,
            "max_tokens": max_tokens,
            "tools": tools,
            "messages": messages,
        }
        if system:
            create_kwargs["system"] = system

        response = self._get_client().messages.create(**create_kwargs)
        usage = getattr(response, "usage", None)
        self.last_usage = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }

        tool_calls = [
            {"name": b.name, "input": b.input, "id": b.id}
            for b in response.content
            if b.type == "tool_use"
        ]
        text = " ".join(b.text for b in response.content if b.type == "text")
        assistant_message = {"role": "assistant", "content": response.content}

        return ToolUseResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            assistant_message=assistant_message,
        )

    def build_tool_result_message(
        self,
        tool_calls: List[Dict[str, Any]],
        results: List[str],
    ) -> Dict[str, Any]:
        """ツール実行結果を Anthropic の tool_result メッセージ形式へ変換する。

        Anthropic 仕様: 同一ターンの全ツール結果を1つの user メッセージに
        まとめ、各ブロックの tool_use_id を LLM が返した id と一致させる。
        """
        content = [
            {
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": result,
            }
            for tc, result in zip(tool_calls, results)
        ]
        return {"role": "user", "content": content}


def create_llm_client(provider: str = None, **kwargs) -> LLMClient:
    provider = (provider or DEFAULT_LLM_PROVIDER).lower()
    if provider == "openai":
        return OpenAIClient(**kwargs)
    if provider == "anthropic":
        return AnthropicClient(**kwargs)
    return GeminiClient(**kwargs)


# Helper functions
def get_available_llm_models() -> List[str]:
    return LLM_MODELS


def get_llm_model_pricing(model_name: str) -> Dict[str, float]:
    return LLM_PRICING.get(model_name, {"input": 0.0, "output": 0.0})


def get_llm_model_limits(model_name: str) -> Dict[str, int]:
    return LLM_LIMITS.get(model_name, {"max_tokens": 0, "max_output": 0})


def get_available_embedding_models() -> List[str]:
    return EMBEDDING_MODELS


def get_embedding_model_pricing(model_name: str) -> float:
    return EMBEDDING_PRICING.get(model_name, 0.0)


def get_embedding_model_dimensions(model_name: str) -> int:
    return EMBEDDING_DIMS.get(model_name, 0)
