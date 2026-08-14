"""
LLMクライアント抽象化レイヤー

Ollama（ローカル LLM）/ Anthropic / OpenAI / Gemini に対応する統一インターフェース
を提供する。
  - テキスト生成: generate_content()
  - 構造化出力: generate_structured()
  - Tool Use（ReAct ループ）: generate_with_tools() / build_tool_result_message()
Embedding は別モジュール（helper_embedding）が担当し、本モジュールは LLM 生成のみ。
Gemini は後方互換のため残置（google.genai は GeminiClient 内で遅延 import）。

【プロバイダー方針】
LLM 用途はすべてローカル（Ollama / 既定は config.py::get_default_ollama_model()）。Embedding（検索）は
Gemini（gemini-embedding-001 / 3072次元）を継続利用するため、本モジュールの
OllamaClient は LLM 生成のみを担当し、Qdrant コレクションには影響しない。
既定プロバイダーは環境変数 LLM_PROVIDER で上書きできる。
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, NamedTuple, Optional, Type

from dotenv import load_dotenv
from pydantic import BaseModel

from config import get_default_ollama_model

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
# httpx は openai SDK の依存なので、openai が入っていれば必ず使える。
# OllamaClient のリクエスト期限（httpx.Timeout）指定に使う。
try:
    import httpx
    from openai import OpenAI
except ImportError:
    httpx = None
    OpenAI = None

import tiktoken

# 注: google-genai（genai / types）は GeminiClient 専用。本プロジェクトの LLM 既定は
# Anthropic のため、google-genai を top-level import せず GeminiClient 内で遅延 import する
# （embedding は別モジュール helper_embedding が担当）。

load_dotenv()

logger = logging.getLogger(__name__)

# --- LLM モデル設定 --- #
# 本プロジェクトの LLM はローカル（Ollama）。Anthropic / Gemini は後方互換のため残置。
LLM_MODELS = [
    "qwen3.5:9b",                 # デフォルト（GRACE 本体・推論／ローカル）
    "gemma4:e4b",                 # 旧デフォルト
    "gemma4:26b-a4b-it-q4_K_M",   # 量子化された上位版
    "qwen2.5:7b",                 # 日本語精度が高い
    "llama3.1:8b",                # 性能・速度のバランス
    "llama3.2",                   # 軽量・高速
    "claude-sonnet-4-6",          # 後方互換（provider="anthropic" 指定時）
    "claude-haiku-4-5-20251001",  # 後方互換（provider="anthropic" 指定時）
    "gemini-2.5-flash",
    "gemini-2.5-flash-preview",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

# 価格は 1K トークンあたりの USD（概算）。
# ⚠️ Ollama はローカル実行のためコストは常に 0。
LLM_PRICING = {
    "qwen3.5:9b"                 : {"input": 0.0, "output": 0.0},
    "gemma4:e4b"                 : {"input": 0.0, "output": 0.0},
    "gemma4:26b-a4b-it-q4_K_M"   : {"input": 0.0, "output": 0.0},
    "qwen2.5:7b"                 : {"input": 0.0, "output": 0.0},
    "llama3.1:8b"                : {"input": 0.0, "output": 0.0},
    "llama3.2"                   : {"input": 0.0, "output": 0.0},
    "claude-sonnet-4-6"          : {"input": 0.003, "output": 0.015},
    "claude-haiku-4-5-20251001"  : {"input": 0.001, "output": 0.005},
    "gemini-2.5-flash"        : {"input": 0.0001, "output": 0.0004},  # Estimated
    "gemini-2.5-flash-preview": {"input": 0.00015, "output": 0.0035},
    "gemini-2.0-flash"        : {"input": 0.0001, "output": 0.0004},
    "gemini-1.5-pro"          : {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash"        : {"input": 0.000075, "output": 0.0003},
}

LLM_LIMITS = {
    "qwen3.5:9b"                 : {"max_tokens": 32768, "max_output": 8192},
    "gemma4:e4b"                 : {"max_tokens": 128000, "max_output": 8192},
    "gemma4:26b-a4b-it-q4_K_M"   : {"max_tokens": 128000, "max_output": 8192},
    "qwen2.5:7b"                 : {"max_tokens": 32768, "max_output": 8192},
    "llama3.1:8b"                : {"max_tokens": 128000, "max_output": 8192},
    "llama3.2"                   : {"max_tokens": 128000, "max_output": 8192},
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

DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

# --- Ollama（ローカル LLM）設定 --- #
# API キー不要。base_url は環境変数 OLLAMA_BASE_URL で上書きできる。
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
# 実体は config.py::get_default_ollama_model() の1箇所のみで管理する
# （同関数が環境変数 OLLAMA_DEFAULT_MODEL の解決も行う）。
DEFAULT_OLLAMA_MODEL = get_default_ollama_model()

# ⚠️ ローカル LLM のリクエスト期限（秒）。
#
# openai SDK の既定は **600 秒 × リトライ 2 回 = 最悪 30 分** ブロックする
# （openai/_constants.py: DEFAULT_TIMEOUT=600 / DEFAULT_MAX_RETRIES=2）。
# 未指定のままだと 9B 級モデルの遅い応答と組み合わさって「実行が止まって
# 見える」ため、ここで必ず有限の期限を入れる。
#
# 既定 180 秒は 9B 級モデルの実測（1 呼び出し 90〜250 秒）に合わせた値。
# **上位ステップのタイムアウト（planner.step_timeout_seconds）より必ず短く**
# すること。逆転すると、ステップ側が先に諦めて HTTP だけが生き残り、
# 捨てたはずの生成が Ollama の GPU を占有して後続を遅らせる。
DEFAULT_OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "180"))
# 接続確立だけは短く切る（Ollama 未起動を 5 秒で検出する）。
DEFAULT_OLLAMA_CONNECT_TIMEOUT = 5.0
# ローカルは呼び出し側（planner の retry / executor の fallback）が再試行を
# 持つため、SDK 側の自動リトライは 1 回に抑える。
DEFAULT_OLLAMA_MAX_RETRIES = 1


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


def _resolve_schema_refs(schema: dict) -> dict:
    """JSON Schema の $ref / $defs を解決してフラットな構造に変換する。

    Ollama のローカルモデル（gemma4:e4b, llama3.2 等）は $ref を含む複雑な
    スキーマを解釈できず、**スキーマ定義そのものをオウム返し**してしまう。
    Pydantic の model_json_schema() はネストしたモデルに対して $defs/$ref を
    生成するため、Ollama へ渡す前に必ず本関数で展開する。
    """
    defs = schema.get("$defs", {})

    def resolve(obj: Any, depth: int = 0) -> Any:
        # 自己参照スキーマで無限再帰しないよう深さで打ち切る
        if depth > 10:
            return obj
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                return resolve(defs.get(ref_name, obj), depth + 1)
            return {
                k: resolve(v, depth + 1)
                for k, v in obj.items()
                if k not in ("$defs", "title")
            }
        if isinstance(obj, list):
            return [resolve(item, depth + 1) for item in obj]
        return obj

    return resolve(schema)


def _block_attr(block: Any, name: str) -> Any:
    """dict / SDK オブジェクトのどちらでもブロック属性を取り出す。"""
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _to_openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """会話履歴を Anthropic 形式から OpenAI（Ollama）形式へ変換する。

    ReAct ループの呼び出しサイト（services/agent_service.py）は Anthropic の
    ブロック形式で履歴を積む:

        {"role": "assistant", "content": [ {type:"text"...}, {type:"tool_use"...} ]}
        {"role": "user",      "content": [ {type:"tool_result", tool_use_id:...} ]}

    Ollama（OpenAI 互換）はこの形を受け付けず、次の形を要求する:

        {"role": "assistant", "content": "...", "tool_calls": [...]}
        {"role": "tool", "tool_call_id": "...", "content": "..."}

    呼び出しサイトを Anthropic 版と共通のまま保つため、変換はここで吸収する。
    既に OpenAI 形式（content が str / role=="tool"）のメッセージは素通しする。
    """
    out: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        # 既に OpenAI 形式のものは素通し
        if role == "tool":
            out.append(msg)
            continue
        if isinstance(content, str) or content is None:
            if role == "assistant" and msg.get("tool_calls"):
                out.append(msg)
            else:
                out.append({"role": role, "content": content or ""})
            continue
        if not isinstance(content, list):
            out.append({"role": role, "content": str(content)})
            continue

        # Anthropic ブロック形式を分解する
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        tool_messages: List[Dict[str, Any]] = []
        for block in content:
            btype = _block_attr(block, "type")
            if btype == "text":
                text_parts.append(_block_attr(block, "text") or "")
            elif btype == "tool_use":
                tool_calls.append({
                    "id"      : _block_attr(block, "id"),
                    "type"    : "function",
                    "function": {
                        "name"     : _block_attr(block, "name"),
                        "arguments": json.dumps(
                            _block_attr(block, "input") or {}, ensure_ascii=False
                        ),
                    },
                })
            elif btype == "tool_result":
                tool_messages.append({
                    "role"        : "tool",
                    "tool_call_id": _block_attr(block, "tool_use_id"),
                    "content"     : str(_block_attr(block, "content") or ""),
                })

        if role == "assistant":
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            out.append(assistant_msg)
        else:
            # user ターンの tool_result は独立した tool メッセージへ展開する
            out.extend(tool_messages)
            joined = "".join(text_parts).strip()
            if joined:
                out.append({"role": "user", "content": joined})

    return out


def _parse_text_tool_calls(text: str) -> List[Dict[str, Any]]:
    """テキストで返されたツール呼び出しをパースする。

    gemma4:e4b 等は tool_calls を構造化レスポンスではなくテキストで返すことが
    あるため（finish_reason=="stop" かつ tool_calls=None）、本文から拾って
    構造化形式へ復元するフォールバック。

    対応フォーマット:
      1. Gemma4 形式:   Action:tool_name{key:<|"|>value<|"|>}
      2. JSON 辞書形式: {"name": "tool_name", "parameters": {...}}
      3. 簡易 KV 形式:  Action:tool_name Args: {"key": "value"}
    """
    import re
    import uuid

    result: List[Dict[str, Any]] = []

    def _new_id() -> str:
        return f"call_{uuid.uuid4().hex[:8]}"

    # --- フォーマット1: Gemma4 ネイティブ形式 ---
    for tool_name, args_str in re.findall(r'Action:(\w+)\{([^}]*)\}', text):
        args: Dict[str, Any] = {}
        # <|"|>value<|"|> トークン形式
        for km in re.finditer(r'(\w+):<[|]"[|]>([^<]*)<[|]"[|]>', args_str):
            args[km.group(1)] = km.group(2).strip()
        if not args:  # fallback: key:"value"
            for km in re.finditer(r'(\w+):\s*"([^"]*)"', args_str):
                args[km.group(1)] = km.group(2)
        if not args:  # fallback: key:value（クォートなし）
            for km in re.finditer(r'(\w+):\s*([^\s,}]+)', args_str):
                args[km.group(1)] = km.group(2).strip()
        if tool_name:
            result.append({"name": tool_name, "input": args, "id": _new_id()})
    if result:
        return result

    # --- フォーマット2: JSON 辞書形式 ---
    # ⚠️ 正規表現で `{...}` を切り出すと、ネストした "parameters": {...} を含む
    #    実際のツール呼び出しにマッチできない。`raw_decode` で括弧の対応を
    #    取りながら走査する。
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        start = text.find("{", pos)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            pos = start + 1
            continue
        pos = end
        if not isinstance(obj, dict):
            continue
        tool_name = obj.get("name") or obj.get("tool")
        args = obj.get("parameters") or obj.get("args") or obj.get("arguments") or {}
        if tool_name and isinstance(args, dict):
            result.append({"name": tool_name, "input": args, "id": _new_id()})
    if result:
        return result

    # --- フォーマット3: Action:tool_name Args: {...} 形式 ---
    for m in re.finditer(r'Action:\s*(\w+)\s+Args:\s*(\{[^}]*\})', text, re.DOTALL):
        try:
            args = json.loads(m.group(2))
        except Exception:
            args = {}
        result.append({"name": m.group(1), "input": args, "id": _new_id()})

    return result


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


class OllamaClient(LLMClient):
    """Ollama（ローカル LLM）クライアント。

    OpenAI SDK の base_url を Ollama の OpenAI 互換エンドポイントへ差し替えて
    使う。API キーは不要（`api_key="ollama"` はダミー値）。

    OpenAI / Anthropic との主要な差異:
      - Chat Completions のみ対応（Responses API・beta.parse 非対応）
      - 出力上限は **max_tokens**（max_completion_tokens / max_output_tokens 非対応）
      - 構造化出力は JSON モード + フラット化スキーマ + Pydantic parse
      - 拡張思考（thinking）に相当する機能はない

    ⚠️ ReAct の戻り値は **AnthropicClient と同じ `ToolUseResponse`** に揃えてある。
    Ollama ネイティブの `finish_reason=="tool_calls"` は `stop_reason=="tool_use"`
    へ正規化し、会話履歴の Anthropic ブロック形式は `_to_openai_messages()` で
    OpenAI 形式へ変換する。これにより services/agent_service.py の ReAct ループを
    Anthropic 版と共通のまま使える。
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        default_model: str = DEFAULT_OLLAMA_MODEL,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **kwargs,
    ):
        if not OpenAI:
            raise ImportError("openai package is not installed.")
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
        self.timeout = float(timeout) if timeout else DEFAULT_OLLAMA_TIMEOUT
        self.max_retries = (
            DEFAULT_OLLAMA_MAX_RETRIES if max_retries is None else int(max_retries)
        )
        # api_key はダミー。Ollama は認証しない
        #
        # ⚠️ timeout / max_retries は **必ず明示する**。省略すると openai SDK の
        #    既定（600 秒 × 3 回）が効き、1 呼び出しが最大 30 分ブロックする。
        self.client = OpenAI(
            base_url=self.base_url,
            api_key="ollama",
            timeout=httpx.Timeout(self.timeout, connect=DEFAULT_OLLAMA_CONNECT_TIMEOUT),
            max_retries=self.max_retries,
        )
        self.default_model = default_model
        # 他クライアントと配管を揃える（ローカル実行のためコストは常に 0）
        self.last_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        logger.info(
            f"OllamaClient initialized: base_url={self.base_url}, model={default_model}, "
            f"timeout={self.timeout}s, max_retries={self.max_retries}"
        )

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        self.last_usage = {
            "input_tokens" : int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        }

    def generate_content(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        model_name = model or self.default_model
        system = kwargs.pop("system", None)
        # max_completion_tokens / max_output_tokens を max_tokens へ統一する
        max_tokens = (
            kwargs.pop("max_completion_tokens", None)
            or kwargs.pop("max_output_tokens", None)
            or kwargs.pop("max_tokens", 4096)
        )
        temperature = kwargs.pop("temperature", None)
        response_format = kwargs.pop("response_format", None)

        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        create_kwargs: Dict[str, Any] = {
            "model"     : model_name,
            "messages"  : messages,
            "max_tokens": int(max_tokens),
        }
        if temperature is not None:
            create_kwargs["temperature"] = temperature
        if response_format is not None:
            create_kwargs["response_format"] = response_format

        response = self.client.chat.completions.create(**create_kwargs)
        self._record_usage(response)
        choice = response.choices[0]
        text = choice.message.content or ""
        if not text:
            self._log_empty_content(choice, model_name, int(max_tokens))
        return text

    @staticmethod
    def _log_empty_content(choice: Any, model_name: str, max_tokens: int) -> None:
        """本文が空だった理由をログに残す。

        「empty response from LLM」だけでは原因が分からず、モデル名や API を
        疑う方向へ調査が逸れる（実際にそれで時間を溶かした）。thinking 系の
        ローカルモデルは **出力枠を思考で使い切って本文へ到達しない**ことが
        あり、その場合 `finish_reason == "length"` になるか、思考が
        `reasoning_content` 側に出る。どちらなのかをここで明示する。
        """
        finish_reason = getattr(choice, "finish_reason", None)
        reasoning = getattr(choice.message, "reasoning_content", None) or ""
        if reasoning or finish_reason == "length":
            logger.warning(
                f"Ollama 応答の本文が空です（model={model_name}, "
                f"finish_reason={finish_reason}, max_tokens={max_tokens}, "
                f"thinking={len(reasoning)} chars）。"
                "思考で出力枠を使い切った可能性があります。max_output_tokens を上げてください。"
            )
        else:
            logger.warning(
                f"Ollama 応答の本文が空です（model={model_name}, "
                f"finish_reason={finish_reason}, max_tokens={max_tokens}）。"
            )

    def generate_structured(self, prompt: str, response_schema: Type[BaseModel],
                            model: Optional[str] = None, **kwargs) -> BaseModel:
        model_name = model or self.default_model
        system = kwargs.pop(
            "system", "あなたは厳密な JSON ジェネレーターです。JSON のみを出力してください。"
        )
        max_tokens = (
            kwargs.pop("max_completion_tokens", None)
            or kwargs.pop("max_output_tokens", None)
            or kwargs.pop("max_tokens", 8192)
        )
        temperature = kwargs.pop("temperature", 0.1)

        # $ref/$defs を展開したフラットスキーマを渡す（展開しないとオウム返しされる）
        flat_schema = _resolve_schema_refs(response_schema.model_json_schema())
        schema_json = json.dumps(flat_schema, ensure_ascii=False, indent=2)
        augmented_prompt = (
            f"{prompt}\n\n"
            "以下の JSON スキーマに完全に従い、スキーマ定義自体ではなく実際のデータを "
            "JSON で出力してください。\n"
            "余分なテキスト・説明・マークダウンは一切出力しないでください。\n\n"
            f"スキーマ:\n{schema_json}"
        )

        response = self.client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": augmented_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=int(max_tokens),
            temperature=temperature,
        )
        self._record_usage(response)
        raw = response.choices[0].message.content or ""
        try:
            return response_schema.model_validate_json(raw)
        except Exception as e:
            logger.error(f"Ollama JSON parse error: {e}")
            logger.error(f"Raw response: {raw}")
            raise

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        # tiktoken による近似（Ollama にトークンカウント API はない）
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))

    def _supports_tool_calls(self, model_name: str) -> bool:
        """モデルが OpenAI 互換 tools パラメータに対応しているか。

        config 側に制約表があればそれに従う。無ければ対応しているとみなす
        （未知モデルを一律で無効化すると通常の ReAct が動かなくなるため）。
        """
        try:
            from config import OllamaConfig
        except ImportError:
            return True
        checker = getattr(OllamaConfig, "supports_tool_calls", None)
        if not callable(checker):
            return True
        try:
            return bool(checker(model_name))
        except Exception:
            return True

    def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: str = "",
        model: Optional[str] = None,
        max_tokens: int = 4096,
        **kwargs,
    ) -> ToolUseResponse:
        """Tool Use を含む ReAct ループの 1 ステップを実行する。

        戻り値は AnthropicClient と同じ `ToolUseResponse`。`tools=[]` を渡すと
        ツールなしの純粋なテキスト生成（Reflection など）として動作する。
        """
        model_name = model or self.default_model

        # tool calling 非対応モデルは tools を落としてテキスト生成へフォールバック
        if tools and not self._supports_tool_calls(model_name):
            logger.warning(
                f"Model {model_name} does not support tool_calls. "
                "Falling back to text generation."
            )
            tools = []

        full_messages: List[Dict[str, Any]] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(_to_openai_messages(messages))

        create_kwargs: Dict[str, Any] = {
            "model"     : model_name,
            "messages"  : full_messages,
            "max_tokens": int(max_tokens),
        }
        if tools:
            # Anthropic の input_schema 形式 → OpenAI の function 形式へ変換
            create_kwargs["tools"] = [
                {
                    "type"    : "function",
                    "function": {
                        "name"       : t["name"],
                        "description": t.get("description", ""),
                        "parameters" : t.get("input_schema", t.get("parameters", {})),
                    },
                }
                for t in tools
            ]
        if "temperature" in kwargs:
            create_kwargs["temperature"] = kwargs["temperature"]

        response = self.client.chat.completions.create(**create_kwargs)
        self._record_usage(response)
        msg = response.choices[0].message

        tool_calls: List[Dict[str, Any]] = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            tool_calls.append({"name": tc.function.name, "input": args, "id": tc.id})

        text = msg.content or ""
        finish_reason = response.choices[0].finish_reason or "stop"

        # ローカルモデルはツール呼び出しをテキストで返すことがある
        if not tool_calls and text and tools:
            parsed = _parse_text_tool_calls(text)
            if parsed:
                tool_calls = parsed
                logger.debug(f"Text-based tool calls parsed: {[t['name'] for t in parsed]}")

        # tools 指定で完全な空応答になるモデルがあるため、tools 無しで再試行する
        if not text and not tool_calls and tools:
            logger.warning(
                f"Empty response from {model_name} with tools (finish_reason={finish_reason}). "
                "Retrying without tools parameter."
            )
            tool_desc = "\n".join(f'- {t["name"]}: {t.get("description", "")}' for t in tools)
            retry_messages = list(full_messages)
            retry_messages.append({
                "role"   : "user",
                "content": (
                    f"利用可能なツール:\n{tool_desc}\n\n"
                    "ツールを使う場合は次の形式で出力してください:\n"
                    'Action:ツール名{"引数名": "引数値"}\n\n'
                    "ツールが不要な場合は直接回答してください。"
                ),
            })
            retry = self.client.chat.completions.create(
                model=model_name, messages=retry_messages, max_tokens=int(max_tokens),
            )
            self._record_usage(retry)
            text = retry.choices[0].message.content or ""
            finish_reason = retry.choices[0].finish_reason or "stop"
            parsed = _parse_text_tool_calls(text) if text else []
            if parsed:
                tool_calls = parsed

        # Anthropic 互換へ正規化: ツール呼び出しがあれば "tool_use"
        if tool_calls:
            stop_reason = "tool_use"
        elif finish_reason == "stop":
            stop_reason = "end_turn"
        else:
            stop_reason = finish_reason

        # 会話履歴へそのまま追記できる assistant メッセージ（OpenAI 形式）。
        # 次ターンで _to_openai_messages() が素通しする形にしておく。
        assistant_message: Dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id"      : tc["id"],
                    "type"    : "function",
                    "function": {
                        "name"     : tc["name"],
                        "arguments": json.dumps(tc["input"], ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]

        return ToolUseResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            assistant_message=assistant_message,
        )

    def build_tool_result_message(
        self,
        tool_calls: List[Dict[str, Any]],
        results: List[str],
    ) -> Dict[str, Any]:
        """ツール実行結果を会話履歴へ追記できる形式へ変換する。

        ⚠️ AnthropicClient と戻り値の型を揃えるため、**1 個の user メッセージ**
        （Anthropic の tool_result ブロック形式）を返す。Ollama へ送る際は
        `_to_openai_messages()` が role="tool" メッセージ群へ展開する。
        """
        content = [
            {
                "type"       : "tool_result",
                "tool_use_id": tc["id"],
                "content"    : result,
            }
            for tc, result in zip(tool_calls, results)
        ]
        return {"role": "user", "content": content}


def create_llm_client(provider: str = None, **kwargs) -> LLMClient:
    provider = (provider or DEFAULT_LLM_PROVIDER).lower()
    if provider == "ollama":
        return OllamaClient(**kwargs)
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
