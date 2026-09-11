# backend/tests/test_schema_echo.py
"""モデルが **データではなくスキーマ定義** を返す失敗を捕まえるテスト。

## 観測された事実（推測ではない）

実測 2026-09-11（`llama3.2:latest` / チャンク化 Step1 / 20 行）。
期待は `{"paragraphs": [...]}` だが、返ってきた本文は:

    {
      "description": "テキスト構造化の結果",
      "properties": {"paragraphs": {...}},
      "required": ["paragraphs"],
      "type": "object"
    }

**プロンプトに載せたスキーマの逐語コピー**である。3 回リトライしても
1 バイト違わぬ同じものが返り、55 ブロックすべてが機械的分割の
フォールバックへ落ちた。

## なぜ検知できなかったのか

`response_format={"type": "json_object"}` が保証するのは「有効な JSON で
あること」だけで、**どの JSON かは保証しない**。スキーマは
`augmented_prompt` の文章でお願いしていただけなので、指示追従の弱い
モデルは無視できた。そして返ってきたスキーマ定義**自体が有効な JSON
オブジェクト**なので、JSON モードとしては合格してしまう。

表に出るのは pydantic の «Field required [type=missing]» だけで、
「モデルがスキーマを書き写した」という事実は埋もれていた。

⚠️ 実際の Ollama へは接続しない。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from helper.helper_llm import OllamaClient, SchemaEchoError


class _Sentence(BaseModel):
    text: str


class _Paragraph(BaseModel):
    id: int
    sentences: list[_Sentence]


class _StructuralResult(BaseModel):
    # ⚠️ docstring を書かないこと。pydantic はそれを schema の description に
    #    入れるため、本文に参照記法を書くと下の平坦化テストが自分の説明文に
    #    反応してしまう（実際に一度そうなった）。
    paragraphs: list[_Paragraph]


# 実測の逐語（`llama3.2:latest` が返したもの）
SCHEMA_ECHO = json.dumps({
    "description": "テキスト構造化の結果",
    "properties": {"paragraphs": {"items": {"type": "object"}, "type": "array"}},
    "required": ["paragraphs"],
    "type": "object",
}, ensure_ascii=False)

REAL_DATA = json.dumps({"paragraphs": [{"id": 1, "sentences": [{"text": "本文"}]}]})


# =============================================================================
# ① スキーマ制約付きデコードを要求する
# =============================================================================

class TestSchemaConstrainedDecoding:
    """お願い（プロンプト）ではなく制約（文法）でスキーマを守らせること。"""

    def test_requests_json_schema_not_just_json_object(self):
        client, create = _client()
        create.return_value = _completion(REAL_DATA)

        client.generate_structured("なにか", _StructuralResult)

        fmt = create.call_args.kwargs["response_format"]
        assert fmt["type"] == "json_schema", (
            "json_object だけではスキーマを保証できない（オウム返しを許す）"
        )
        assert fmt["json_schema"]["strict"] is True

    def test_schema_is_flattened(self):
        """`$ref`/`$defs` を残したまま渡さないこと。

        Ollama のローカルモデルは `$ref` を解釈できず、やはりオウム返しする。
        """
        client, create = _client()
        create.return_value = _completion(REAL_DATA)

        client.generate_structured("なにか", _StructuralResult)

        sent = json.dumps(create.call_args.kwargs["response_format"])
        assert "$ref" not in sent and "$defs" not in sent

    def test_falls_back_when_server_rejects_json_schema(self):
        """未対応の Ollama では JSON モードへ落として続行すること。"""
        client, create = _client()
        create.side_effect = [
            _bad_request("unsupported response_format type: json_schema"),
            _completion(REAL_DATA),
        ]

        result = client.generate_structured("なにか", _StructuralResult)

        assert result.paragraphs[0].sentences[0].text == "本文"
        assert create.call_args.kwargs["response_format"] == {"type": "json_object"}
        assert client.supports_json_schema is False

    def test_does_not_retry_json_schema_after_a_rejection(self):
        """一度断られたら以降は送らない（毎回 2 往復させない）。"""
        client, create = _client()
        create.side_effect = [
            _bad_request("unsupported response_format type: json_schema"),
            _completion(REAL_DATA),
        ]
        client.generate_structured("なにか", _StructuralResult)

        create.reset_mock()
        create.side_effect = None
        create.return_value = _completion(REAL_DATA)
        client.generate_structured("もう一度", _StructuralResult)

        assert create.call_count == 1
        assert create.call_args.kwargs["response_format"] == {"type": "json_object"}

    def test_timeout_is_not_swallowed_as_unsupported(self):
        """タイムアウトで JSON モードへ落とさない（原因が別物）。"""
        client, create = _client()
        create.side_effect = TimeoutError("Request timed out.")

        with pytest.raises(TimeoutError):
            client.generate_structured("なにか", _StructuralResult)

        assert client.supports_json_schema is True


# =============================================================================
# ② オウム返しを名指しで検知する
# =============================================================================

class TestSchemaEchoDetection:

    def test_raises_schema_echo_error(self):
        """«Field required» ではなく、原因を名指しした例外にすること。"""
        client, create = _client()
        create.return_value = _completion(SCHEMA_ECHO)

        with pytest.raises(SchemaEchoError, match="オウム返し"):
            client.generate_structured("なにか", _StructuralResult)

    def test_message_names_the_expected_keys(self):
        client, create = _client()
        create.return_value = _completion(SCHEMA_ECHO)

        with pytest.raises(SchemaEchoError, match="paragraphs"):
            client.generate_structured("なにか", _StructuralResult)

    def test_real_data_is_not_flagged(self):
        client, create = _client()
        create.return_value = _completion(REAL_DATA)

        result = client.generate_structured("なにか", _StructuralResult)
        assert len(result.paragraphs) == 1

    def test_a_field_literally_named_properties_is_not_flagged(self):
        """⚠️ 「properties を持つ」だけで弾くと正当なデータを誤検知する。

        期待するトップレベルのキーが 1 つでもあれば、それは本物のデータ。
        """
        class _WithProperties(BaseModel):
            paragraphs: list[str]
            properties: dict

        client, create = _client()
        create.return_value = _completion(
            json.dumps({"paragraphs": ["a"], "properties": {"x": 1}})
        )

        result = client.generate_structured("なにか", _WithProperties)
        assert result.properties == {"x": 1}

    def test_detected_even_when_json_schema_is_unavailable(self):
        """古い Ollama（JSON モードしか無い）でも検知すること。

        ⚠️ ここが本丸。`json_schema` が使えるならオウム返しは構造的に
        起きないので、検知器が本当に要るのは**落ちた先**である。
        両方あって初めて「防げるなら防ぐ、防げないなら名指しする」になる。
        """
        client, create = _client()
        client.supports_json_schema = False
        create.return_value = _completion(SCHEMA_ECHO)

        with pytest.raises(SchemaEchoError):
            client.generate_structured("なにか", _StructuralResult)

        assert create.call_args.kwargs["response_format"] == {"type": "json_object"}

    def test_malformed_json_is_left_to_the_parser(self):
        """壊れた JSON はオウム返しではない。従来どおりパースエラーにする。"""
        client, create = _client()
        create.return_value = _completion("{ぜんぜん JSON じゃない")

        with pytest.raises(Exception) as excinfo:
            client.generate_structured("なにか", _StructuralResult)
        assert not isinstance(excinfo.value, SchemaEchoError)


# =============================================================================
# ③ チャンク化はリトライせず即座に止まる
# =============================================================================

class TestChunkingAbortsImmediately:
    """同じ結果になるリトライで時間を捨てないこと。"""

    def test_aborts_without_retrying(self):
        # ⚠️ 本リポジトリに pytest-asyncio は入っていない（CI の依存リストにも
        #    無い）。`@pytest.mark.asyncio` は黙って無視され、コルーチンが
        #    未実行のまま pass になる。asyncio.run で明示的に回す。
        from chunking.async_api_client import AsyncAPIClient, ChunkingAbortedError

        client = AsyncAPIClient(max_workers=1)
        calls = []

        def _boom(*_a, **_k):
            calls.append(1)
            raise SchemaEchoError("llama3.2:latest がスキーマ定義をオウム返ししました")

        client.llm = SimpleNamespace(generate_structured=_boom)

        with pytest.raises(ChunkingAbortedError, match="スキーマ定義"):
            asyncio.run(client.generate_content(
                model="llama3.2:latest", contents="x",
                response_schema=_StructuralResult, task_id="step1_block_0",
            ))

        assert len(calls) == 1, "リトライしてはいけない（結果は同じ）"

    def test_message_suggests_a_working_model(self):
        from chunking.async_api_client import AsyncAPIClient, ChunkingAbortedError

        client = AsyncAPIClient(max_workers=1)
        client.llm = SimpleNamespace(
            generate_structured=MagicMock(side_effect=SchemaEchoError("echo"))
        )

        with pytest.raises(ChunkingAbortedError, match="gemma4:12b-mlx"):
            asyncio.run(client.generate_content(
                model="llama3.2:latest", contents="x",
                response_schema=_StructuralResult, task_id="step1_block_0",
            ))


# =============================================================================
# helpers
# =============================================================================

def _client(**kwargs) -> tuple[OllamaClient, MagicMock]:
    client = OllamaClient(**kwargs)
    create = MagicMock()
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client, create


def _completion(content: str) -> SimpleNamespace:
    message = SimpleNamespace(role="assistant", content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=message)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
    )


def _bad_request(message: str) -> Exception:
    exc = Exception(message)
    exc.status_code = 400
    return exc
