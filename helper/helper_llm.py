"""
LLMクライアント抽象化レイヤー

Ollama（ローカル LLM）/ OpenAI / Gemini に対応する統一インターフェース
を提供する。
  - テキスト生成: generate_content()
  - 構造化出力: generate_structured()
  （Tool Use の generate_with_tools() / build_tool_result_message() は、唯一の呼び出し元だった
   Legacy ReAct〔services/agent_service.py〕とともに 2026-10-10 に削除した）
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
from typing import Any, Dict, List, Optional, Type

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
# Ollama のため、google-genai を top-level import せず GeminiClient 内で遅延 import する
# （embedding は別モジュール helper_embedding が担当）。

load_dotenv()

logger = logging.getLogger(__name__)

# --- LLM モデル設定 --- #
# 本プロジェクトの LLM はローカル（Ollama）。Gemini は後方互換のため残置。
LLM_MODELS = [
    "gemma4:12b-mlx",             # 7.7 GB・軽量（2026-10-03 までの既定）
    "gemma4:e4b-mlx",             # 9.5 GB
    "gemma4:26b-mlx",             # 18 GB・上位
    "gemma4:26b-a4b-it-qat",      # 15 GB・既定（QAT 版。GGUF・非 MLX）
    "qwen3.8:27b-mlx",            # 18 GB・上位（多言語）
    "llama3.2:latest",            # 2.0 GB・軽量/高速
    "gemini-2.5-flash",
    "gemini-2.5-flash-preview",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

# 価格は 1K トークンあたりの USD（概算）。
# ⚠️ Ollama はローカル実行のためコストは常に 0。
LLM_PRICING = {
    "gemma4:12b-mlx"             : {"input": 0.0, "output": 0.0},
    "gemma4:e4b-mlx"             : {"input": 0.0, "output": 0.0},
    "gemma4:26b-mlx"             : {"input": 0.0, "output": 0.0},
    "gemma4:26b-a4b-it-qat"      : {"input": 0.0, "output": 0.0},
    "qwen3.8:27b-mlx"            : {"input": 0.0, "output": 0.0},
    "llama3.2:latest"            : {"input": 0.0, "output": 0.0},
    "gemini-2.5-flash"        : {"input": 0.0001, "output": 0.0004},  # Estimated
    "gemini-2.5-flash-preview": {"input": 0.00015, "output": 0.0035},
    "gemini-2.0-flash"        : {"input": 0.0001, "output": 0.0004},
    "gemini-1.5-pro"          : {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash"        : {"input": 0.000075, "output": 0.0003},
}

LLM_LIMITS = {
    "gemma4:12b-mlx"             : {"max_tokens": 128000, "max_output": 8192},
    "gemma4:e4b-mlx"             : {"max_tokens": 128000, "max_output": 8192},
    "gemma4:26b-mlx"             : {"max_tokens": 128000, "max_output": 8192},
    "gemma4:26b-a4b-it-qat"      : {"max_tokens": 128000, "max_output": 8192},
    "qwen3.8:27b-mlx"            : {"max_tokens": 32768, "max_output": 8192},
    "llama3.2:latest"            : {"max_tokens": 128000, "max_output": 8192},
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
#
# ⚠️ **`openai` SDK を使うのは正しい。外部 API へは 1 バイトも出ない。**
#
# Ollama は OpenAI 互換エンドポイント（`/v1/chat/completions`）を提供して
# おり、`openai` Python SDK はその標準クライアントである。したがって
# ログに現れる
#
#     DEBUG openai._base_client - Request options: ...
#     INFO  httpx - HTTP Request: POST http://localhost:11434/v1/chat/completions
#
# は **ロガー名が `openai` なだけ**で、宛先は localhost:11434（＝手元の
# Ollama）である。api.openai.com へは接続していないし、API キーも要らない
# （下の `api_key="ollama"` は SDK が空文字を拒否するためのダミー）。
#
# 「ローカル LLM なのに openai を使っていて、しかもタイムアウトが多発して
# いる」のは因果が逆で、タイムアウトの原因は SDK ではなくモデルの生成速度
# （9B〜26B 級で 1 呼び出し 90〜250 秒）と、その予算設計にある。
# → `DEFAULT_OLLAMA_TIMEOUT` / `DEFAULT_OLLAMA_MAX_RETRIES` を参照。
#
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
# ⚠️ SDK 側の自動リトライは **0**（無効）。
#
# openai SDK の timeout はリクエスト 1 本あたりの期限なので、実際に費やす
# 時間は `timeout × (max_retries + 1)` になる。以前ここが 1 だったため
# 180s × 2 = 360s となり、上位の step_timeout_seconds(240s) を追い越して
# いた。実測ログ:
#     14:57:31 開始 → 15:00:31 Retrying(180s 経過) → 15:01:31 step timeout(240s)
# 2 回目は必ず途中で殺されるので、60 秒を捨てるだけの純粋な無駄だった。
#
# そもそもローカル LLM の timeout はネットワーク瞬断ではなく「モデルが遅い／
# 出力を終端できない」ことが原因で、同じプロンプトを投げ直しても結果は同じ。
# 再試行が要る場面は呼び出し側（planner の retry / executor の fallback /
# ReasoningTool の最小プロンプト再試行）が持っているので、SDK 層では持たない。
#
# ⚠️ 変更する場合は必ず
#     llm.timeout × (max_retries + 1) < planner.step_timeout_seconds
# を満たすこと（tests/test_timeout_budget.py が検証する）。
DEFAULT_OLLAMA_MAX_RETRIES = 0

# ⚠️ 思考（thinking / reasoning）を抑止する。
#
# ## なぜ必要か（実測で確定した事実）
#
# gemma4:26b-a4b-it-qat は思考モデルで、**本文を一度も出さないことがある**。
# 空応答時の診断ログがそれを示した:
#
#     finish_reason=length, max_tokens=4096, completion_tokens=2766,
#     thinking=10007 chars (key=reasoning),
#     message_keys=['reasoning', 'role']      ← content が **存在しない**
#
# `content` というキー自体が応答に無い。生成した 10007 文字はすべて
# `reasoning` に入り、本文には 1 文字も到達していない。
#
# ⚠️ 以前の推測はどちらも外れていた:
#   - 「JSON スキーマの出力が枠に収まらない」→ `response_format=なし` の
#     素のテキスト生成でも同じく空。JSON は無関係。
#   - 「枠を上げれば直る／枠は関係ない」→ 512 / 4096 / 8192 のいずれでも
#     同じ。枠は主因ではなく、**思考が枠を食い尽くす**のが主因。
#
# ## 何を送るか
#
# Ollama の OpenAI 互換エンドポイントは `reasoning_effort` を受け取り、
# "none" で思考を無効化する。ただし対応は Ollama のバージョン依存なので、
# 送って拒否されたら**自動的に外して再送し、以降そのクライアントでは
# 送らない**（機能検出）。環境変数で無効化もできる。
DEFAULT_OLLAMA_REASONING_EFFORT = os.getenv("OLLAMA_REASONING_EFFORT", "none")


class SchemaEchoError(ValueError):
    """モデルがデータではなく **JSON Schema 定義そのもの** を返した。

    ⚠️ **リトライしても直らない。** プロンプトもスキーマも同じものを送るので、
    同じ出力が返る。実測（llama3.2:latest / 2026-09-11）では 3 回とも
    1 バイト違わぬ同じスキーマが返り、55 ブロックすべてが機械的分割の
    フォールバックへ落ちた。

    `ValueError` を単に投げると呼び出し側のリトライ経路に混ざるため、
    「再試行に意味が無い失敗」として区別できる型にしてある。
    """


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


class OllamaClient(LLMClient):
    """Ollama（ローカル LLM）クライアント。

    OpenAI SDK の base_url を Ollama の OpenAI 互換エンドポイントへ差し替えて
    使う。API キーは不要（`api_key="ollama"` はダミー値）。

    OpenAI との主要な差異:
      - Chat Completions のみ対応（Responses API・beta.parse 非対応）
      - 出力上限は **max_tokens**（max_completion_tokens / max_output_tokens 非対応）
      - 構造化出力は JSON モード + フラット化スキーマ + Pydantic parse
      - 拡張思考（thinking）に相当する機能はない

    Tool Use（`generate_with_tools()`）は、唯一の呼び出し元だった Legacy ReAct
    （services/agent_service.py）とともに 2026-10-10 に削除した。
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
        # 思考抑止。"" / "off" で無効。送って拒否されたら機能検出で自動的に外す。
        self.reasoning_effort: Optional[str] = (
            kwargs.pop("reasoning_effort", None) or DEFAULT_OLLAMA_REASONING_EFFORT
        ) or None
        if self.reasoning_effort in ("off", "false", "0"):
            self.reasoning_effort = None

        # スキーマ制約付きデコード（Ollama の structured outputs）に対応するか。
        # 対応は Ollama のバージョン依存なので、実行時に検出して以降は送らない。
        self.supports_json_schema: bool = True
        # ⚠️ 直近の呼び出しが「思考だけ返して本文ゼロ」だったか。
        #    上位はこれを見て「再試行・リプランしても無駄」と判断できる。
        #    同じプロンプトを投げ直しても同じ思考を繰り返すだけなので、
        #    ここを見ずに再試行すると 1 回 90〜250 秒を延々と捨てる。
        self.last_thinking_only: bool = False
        logger.info(
            f"OllamaClient initialized: base_url={self.base_url}, model={default_model}, "
            f"timeout={self.timeout}s, max_retries={self.max_retries}, "
            f"reasoning_effort={self.reasoning_effort or 'なし'}"
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

        response = self._create_completion(create_kwargs)
        self._record_usage(response)
        choice = response.choices[0]
        text = choice.message.content or ""
        thinking, _ = self._extract_thinking(choice.message)
        # 本文ゼロ かつ 思考あり ＝「思考だけで力尽きた」。上位が再試行を
        # 諦められるよう記録する（同じプロンプトを投げ直しても同じになる）。
        self.last_thinking_only = bool(not text and thinking)
        if not text:
            self._log_empty_content(
                choice, model_name, int(max_tokens),
                completion_tokens=self.last_usage.get("output_tokens", 0),
                prompt_tokens=self.last_usage.get("input_tokens", 0),
                response_format=response_format,
            )
        return text

    def _create_completion(self, create_kwargs: Dict[str, Any]) -> Any:
        """`reasoning_effort` を付けて送り、拒否されたら外して再送する。

        `reasoning_effort` は Ollama のバージョンによって未対応で、その場合は
        400 などで弾かれる。対応可否を実行時に検出して以降は送らない
        （＝バージョン判定をハードコードしない）。
        """
        if not self.reasoning_effort:
            return self.client.chat.completions.create(**create_kwargs)

        try:
            return self.client.chat.completions.create(
                reasoning_effort=self.reasoning_effort, **create_kwargs
            )
        except Exception as e:
            if not self._looks_like_unsupported_param(e):
                raise
            # ⚠️ **別のパラメータが原因なら、ここで握らない。**
            #    「そんなパラメータは知らない」の判定は文言ベースなので、
            #    `response_format: json_schema` の拒否まで拾ってしまう。
            #    そのまま reasoning_effort を外して再送すると:
            #      - 2 回目も同じ理由で失敗する（無駄な 1 往復）
            #      - しかも **思考抑止が恒久的に無効化される**
            #        （#88 で直したばかりの経路が、無関係な理由で壊れる）
            #    呼び出し側に返して、本当の原因に対処させる。
            if self._names_another_param(e):
                raise
            logger.warning(
                f"この Ollama は reasoning_effort に未対応のため無効化します: {e}. "
                "思考モデルを使う場合、本文が空になることがあります"
                "（OLLAMA_DEFAULT_MODEL で非思考モデルを検討してください）。"
            )
            self.reasoning_effort = None
            return self.client.chat.completions.create(**create_kwargs)

    @staticmethod
    def _looks_like_unsupported_param(exc: Exception) -> bool:
        """「そんなパラメータは知らない」系のエラーか（≠ 通信・タイムアウト）。

        タイムアウトやサーバ落ちまで握り潰すと、無駄な 2 回目を投げてしまう。
        パラメータ不正だと読める場合だけ再送する。
        """
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if status is not None and int(status) not in (400, 404, 422):
            return False
        message = str(exc).lower()
        markers = ("reasoning_effort", "unknown", "unsupported", "unexpected",
                   "not supported", "invalid", "extra fields", "unrecognized")
        return any(m in message for m in markers)

    @staticmethod
    def _names_another_param(exc: Exception) -> bool:
        """エラーが `reasoning_effort` 以外のパラメータを名指ししているか。

        名指しがあるならそれが原因で、reasoning_effort を外しても直らない。
        """
        message = str(exc).lower()
        if "reasoning_effort" in message:
            return False
        return any(
            name in message
            for name in ("response_format", "json_schema", "tools", "tool_choice")
        )

    # 「思考」を載せてくるフィールド名は提供側でぶれる。
    # reasoning_content（OpenAI 互換の慣習）/ thinking（Ollama の native 寄り）/
    # reasoning のいずれかで返りうるので、**全部見る**。
    #
    # ⚠️ openai SDK の応答モデルは `extra="allow"` なので、未知フィールドも
    #    属性として保持される。1 つだけ見て「thinking=0 chars」と結論すると、
    #    別キーに中身が入っていても気づけない（実際にそれで誤診した）。
    _THINKING_KEYS = ("reasoning_content", "thinking", "reasoning")

    @classmethod
    def _extract_thinking(cls, message: Any) -> tuple[str, Optional[str]]:
        """思考テキストと、それが入っていたキー名を返す。無ければ ("", None)。"""
        for key in cls._THINKING_KEYS:
            value = getattr(message, key, None)
            if isinstance(value, str) and value:
                return value, key
        return "", None

    @staticmethod
    def _message_keys(message: Any) -> list[str]:
        """応答 message が実際に持っているキー名（中身が None のものは除く）。

        「どのフィールドに何が入っていたのか」が分からないと、空応答の原因を
        推測でしか語れなくなる。openai SDK の pydantic モデルから素直に拾う。
        """
        dumped = getattr(message, "model_dump", None)
        if callable(dumped):
            try:
                return sorted(k for k, v in dumped().items() if v not in (None, "", [], {}))
            except Exception:  # pragma: no cover - 応答形状は提供側依存
                pass
        return sorted(k for k in vars(message) if not k.startswith("_"))

    @classmethod
    def _log_empty_content(
        cls,
        choice: Any,
        model_name: str,
        max_tokens: int,
        completion_tokens: int = 0,
        prompt_tokens: int = 0,
        response_format: Any = None,
    ) -> None:
        """本文が空だった理由をログに残す。

        「empty response from LLM」だけでは原因が分からず、モデル名や API を
        疑う方向へ調査が逸れる（実際にそれで時間を溶かした）。

        ## ⚠️ ここは「観測」を出す場所であって「断定」を出す場所ではない

        以前ここには「`finish_reason=length` は枠を上げても直らない」と
        **断定**が書いてあった。だが同一実行のログがそれを否定した:

            17:42:29  evaluate_final       → 成功（JSON+schema・枠 1024）
            17:44:04  groundedness verify  → 空  （JSON+schema・枠 1024）

        同じ枠・同じ JSON モード・同じモデルで、片方は通り片方は空になる。
        つまり効いているのは枠そのものではなく **その呼び出しが要求する
        出力量**であり、「枠を上げても無駄」は誤った一般化だった。

        断定を書けるだけの情報が無かったのが根本原因なので、ここでは
        判断材料を全部出す:

        - `completion_tokens`: 本当に枠まで生成したのか（0 なら話が逆）
        - `prompt_tokens`: 入力が大きすぎないか
        - 思考フィールド: **候補キーを全部**見る（1 つだけ見て誤診した）
        - `message` が実際に持つキー: 中身がどこへ行ったのか
        - `response_format`: JSON 制約下かどうか
        """
        finish_reason = getattr(choice, "finish_reason", None)
        message = choice.message
        thinking, thinking_key = cls._extract_thinking(message)
        fmt = (response_format or {}).get("type") if isinstance(response_format, dict) else None

        head = (
            f"Ollama 応答の本文が空です（model={model_name}, "
            f"finish_reason={finish_reason}, max_tokens={max_tokens}, "
            f"completion_tokens={completion_tokens}, prompt_tokens={prompt_tokens}, "
            f"response_format={fmt or 'なし'}, "
            f"thinking={len(thinking)} chars"
            f"{f' (key={thinking_key})' if thinking_key else ''}, "
            f"message_keys={cls._message_keys(message)}）"
        )

        if thinking:
            logger.warning(
                f"{head}: 思考（{thinking_key}）だけを返し本文へ到達していません。"
                "max_output_tokens を上げるか、思考を出さないモデルを検討してください。"
            )
        elif finish_reason == "length" and completion_tokens >= max_tokens:
            # 枠いっぱいまで生成した = 出力が枠に入っていない。
            logger.warning(
                f"{head}: **出力が枠に収まっていません**"
                f"（{completion_tokens}/{max_tokens} トークン生成）。"
                "この呼び出しの max_output_tokens を上げるか、"
                "求める出力量そのものを減らしてください"
                "（日本語は JSON の \\uXXXX エスケープで約 3 倍に膨らみます）。"
            )
        elif finish_reason == "length":
            # 枠に届いていないのに length。提供側の挙動を疑う段階。
            logger.warning(
                f"{head}: finish_reason=length なのに生成トークンが枠に届いていません"
                f"（{completion_tokens}/{max_tokens}）。"
                "枠ではなく Ollama 側の打ち切り（grammar 制約・コンテキスト長）を"
                "疑ってください。"
            )
        else:
            logger.warning(f"{head}。")

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

        # ⚠️ **`{"type": "json_object"}` だけでは足りない。**
        #
        # JSON モードが保証するのは「有効な JSON であること」だけで、
        # **どの JSON かは保証しない。** スキーマは augmented_prompt の
        # 文章でお願いしているにすぎず、指示追従の弱いモデルはこれを無視して
        # **スキーマ定義そのものをそのまま返す**。それ自体が有効な JSON
        # オブジェクトなので JSON モードは満たされ、何も検知されない。
        #
        # 実測 2026-09-11（llama3.2:latest / チャンク化 Step1）— 返ってきた本文:
        #
        #     {"description": "テキスト構造化の結果",
        #      "properties": {"paragraphs": {...}},
        #      "required": ["paragraphs"], "type": "object"}
        #
        # 期待は `{"paragraphs": [...]}`。3 回リトライしても同じものが返り、
        # 全ブロックが機械的分割のフォールバックへ落ちた。
        #
        # Ollama の structured outputs（`json_schema`）は**デコードを文法で
        # 拘束する**ので、スキーマに無いキーは物理的に出力できない。お願いでは
        # なく制約になる。対応はバージョン依存なので、拒否されたら
        # `json_object` へ落として以降は送らない（reasoning_effort と同じ方式）。
        response_format = self._structured_response_format(flat_schema, response_schema.__name__)

        # ⚠️ **`_create_completion()` を通す。** 直接 `client.chat.completions
        #    .create()` を呼ぶと `reasoning_effort` が送られない。
        #
        #    起動ログは `reasoning_effort=none` と出るのに、この経路だけ
        #    思考抑止が効かない、という食い違いが起きていた。実際の
        #    チャンク化ログ（2026-09-11）にその両方が並んでいる:
        #
        #      OllamaClient initialized: ... reasoning_effort=none   ← 設定はされている
        #      Request options: {... 'max_tokens': 8192,             ← だが送信 payload に
        #                            'response_format': {...}}          reasoning_effort が無い
        #
        #    思考モデルは本文へ到達する前に枠を使い切るため、抑止が効かない
        #    経路では 1 リクエストが上限まで走り、タイムアウトで切れ続ける。
        create_kwargs = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": augmented_prompt},
            ],
            "response_format": response_format,
            "max_tokens": int(max_tokens),
            "temperature": temperature,
        }
        try:
            response = self._create_completion(create_kwargs)
        except Exception as e:
            if response_format.get("type") != "json_schema" or not self._looks_like_unsupported_param(e):
                raise
            logger.warning(
                f"この Ollama は structured outputs（json_schema）に未対応のため "
                f"JSON モードへ落とします: {e}. "
                "指示追従の弱いモデルではスキーマをオウム返しすることがあります。"
            )
            self.supports_json_schema = False
            response_format = {"type": "json_object"}
            create_kwargs["response_format"] = response_format
            response = self._create_completion(create_kwargs)

        self._record_usage(response)
        choice = response.choices[0]
        raw = choice.message.content or ""

        # ⚠️ **空のときは観測値を残す。** ここで `model_validate_json("")` に
        #    渡すと «Invalid JSON: EOF while parsing» しか残らず、
        #    finish_reason も生成トークン数も思考の有無も失われる。
        #    `generate_content()` 側と同じ診断を出す。
        if not raw:
            self._log_empty_content(
                choice, model_name, int(max_tokens),
                completion_tokens=self.last_usage.get("output_tokens", 0),
                prompt_tokens=self.last_usage.get("input_tokens", 0),
                response_format=response_format,
            )

        # ⚠️ **オウム返しは「パース失敗」として出すと原因が見えない。**
        #    pydantic は «Field required [type=missing]» としか言わないので、
        #    モデルがデータではなくスキーマを返したという事実が埋もれる。
        if self._looks_like_schema_echo(raw, flat_schema):
            logger.error(
                f"Ollama がデータではなく**スキーマ定義そのもの**を返しました"
                f"（model={model_name}, json_schema={'有効' if response_format.get('type') == 'json_schema' else '無効'}）。"
                " リトライしても同じ結果になります。"
                " 対処: structured outputs に対応した Ollama へ更新するか、"
                "指示追従の強いモデル（gemma4:12b-mlx 等）を使ってください。"
            )
            logger.error(f"Raw response: {raw[:500]}")
            raise SchemaEchoError(
                f"{model_name} がスキーマ定義をオウム返ししました"
                f"（期待するキー: {sorted(flat_schema.get('properties', {}))}）"
            )

        try:
            return response_schema.model_validate_json(raw)
        except Exception as e:
            logger.error(f"Ollama JSON parse error: {e}")
            logger.error(f"Raw response: {raw}")
            raise

    def _structured_response_format(self, flat_schema: dict, name: str) -> Dict[str, Any]:
        """スキーマ制約付きデコードを要求する `response_format` を組み立てる。

        未対応と分かっている Ollama では JSON モードへ倒す（実行時に検出した
        結果を `supports_json_schema` に覚えてあるので、毎回試さない）。
        """
        if not self.supports_json_schema:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": flat_schema, "strict": True},
        }

    @staticmethod
    def _looks_like_schema_echo(raw: str, flat_schema: dict) -> bool:
        """返ってきたのがデータではなく **スキーマ定義** かどうか。

        判定は 2 段階にする。「`properties` を持つ」だけで弾くと、たまたま
        `properties` という名のフィールドを持つ正当なデータまで誤検知する。

          1. 期待するトップレベルのキーが **1 つでもあれば** 本物のデータ
          2. そのうえで JSON Schema 特有のキーを持つならオウム返し
        """
        try:
            data = json.loads(raw)
        except Exception:
            return False
        if not isinstance(data, dict):
            return False

        expected = set(flat_schema.get("properties", {}))
        if expected & set(data):
            return False

        return any(key in data for key in ("properties", "$schema", "$defs"))

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        # tiktoken による近似（Ollama にトークンカウント API はない）
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))

def create_llm_client(provider: str = None, **kwargs) -> LLMClient:
    """プロバイダ名から LLM クライアントを作る（既定は環境変数 LLM_PROVIDER、無ければ ollama）。

    ⚠️ 未知のプロバイダ名は ValueError。2026-09-26 まで最後の分岐で黙って GeminiClient に
    していたため、"olama" のような打ち間違いでも Gemini LLM API を呼んでいた。
    """
    provider = (provider or DEFAULT_LLM_PROVIDER).lower()
    if provider == "ollama":
        return OllamaClient(**kwargs)
    if provider == "openai":
        return OpenAIClient(**kwargs)
    if provider in ("gemini", "google"):
        return GeminiClient(**kwargs)
    raise ValueError(
        f"未知の LLM プロバイダです: {provider!r}"
        "（ollama / openai / gemini のいずれか。既定は環境変数 LLM_PROVIDER）"
    )


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
