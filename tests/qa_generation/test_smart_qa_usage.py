"""B-1: SmartQAGenerator のトークン使用量配線（process_chunk の 'usage'）。

実 LLM API 不要。統一 LLM クライアント（create_llm_client）をモックし、
generate_structured が返す解析済み SmartQAResult が process_chunk の戻り値に
正しく載ること、および per-call トークン使用量（AnthropicClient.last_usage 由来）が
process_chunk の 'usage' に伝播することを検証する。失敗時は usage がゼロになる。
"""
from unittest.mock import MagicMock, patch

import qa_generation.smart_qa_generator as m
from qa_generation.smart_qa_generator import SmartQAResult


def _make_generator(last_usage=None):
    with patch.object(m, "create_llm_client") as mock_factory:
        client = MagicMock()
        # AnthropicClient.last_usage を模す（None の場合は属性を持たせない）
        if last_usage is not None:
            client.last_usage = last_usage
        else:
            # dict でない場合は伝播せずゼロ維持されることを確認するため MagicMock のまま
            client.last_usage = MagicMock()
        mock_factory.return_value = client
        return m.SmartQAGenerator(api_key="x")


def _fake_result(qa_count=1):
    return SmartQAResult(
        qa_count=qa_count,
        qa_pairs=[{"question": "Q", "answer": "A", "topic": "t"}] * qa_count,
    )


def test_process_chunk_propagates_usage():
    """client.last_usage が dict なら process_chunk['usage'] に伝播する。"""
    gen = _make_generator(last_usage={"input_tokens": 120, "output_tokens": 45})
    gen.client.generate_structured.return_value = _fake_result(1)

    out = gen.process_chunk("text")

    assert out["success"] is True
    assert out["usage"] == {"input_tokens": 120, "output_tokens": 45}
    assert len(out["qa_pairs"]) == 1


def test_non_dict_usage_stays_zero():
    """client.last_usage が dict でない場合はゼロ維持（堅牢化）。"""
    gen = _make_generator(last_usage=None)
    gen.client.generate_structured.return_value = _fake_result(1)

    out = gen.process_chunk("text")

    assert out["success"] is True
    assert out["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_failure_returns_zero_usage():
    gen = _make_generator(last_usage={"input_tokens": 9, "output_tokens": 9})
    gen.client.generate_structured.side_effect = RuntimeError("boom")

    out = gen.process_chunk("text")

    assert out["success"] is False
    assert out["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_empty_result_propagates_usage():
    """qa_count=0 のレスポンスでも usage は伝播する。"""
    gen = _make_generator(last_usage={"input_tokens": 7, "output_tokens": 0})
    gen.client.generate_structured.return_value = SmartQAResult(qa_count=0, qa_pairs=[])

    out = gen.process_chunk("text")

    assert out["success"] is True
    assert out["usage"] == {"input_tokens": 7, "output_tokens": 0}
    assert out["qa_pairs"] == []
