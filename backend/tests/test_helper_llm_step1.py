# test_helper_llm_step1.py
import os

import pytest

# これらは実際の Gemini API を呼び出すライブテスト。
# APIキー未設定のオフライン環境ではスキップする。
# 注意: tests/grace/conftest.py が GOOGLE_API_KEY をプレースホルダ "test-api-key"
# で setdefault するため、存在チェックだけでは不十分。プレースホルダも未設定扱い。
_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not _api_key or _api_key == "test-api-key":
    pytest.skip(
        "live Gemini API tests require a real GEMINI_API_KEY/GOOGLE_API_KEY",
        allow_module_level=True,
    )

from helper.helper_llm import GeminiClient  # noqa: E402


def test_generate_content():
    """テキスト生成の基本動作確認"""
    client = GeminiClient()
    result = client.generate_content("1+1は何ですか？一言で答えてください。")
    assert result is not None
    assert len(result) > 0
    print(f"✅ generate_content: {result}")

def test_count_tokens():
    """トークンカウントの動作確認"""
    client = GeminiClient()
    count = client.count_tokens("これはテスト文です。")
    assert count > 0
    print(f"✅ count_tokens: {count}")

def test_generate_structured():
    """構造化出力の動作確認"""
    from pydantic import BaseModel

    class SimpleAnswer(BaseModel):
        answer: str
        confidence: float

    client = GeminiClient()
    result = client.generate_structured(
        prompt="日本の首都はどこですか？",
        response_schema=SimpleAnswer
    )
    assert isinstance(result, SimpleAnswer)
    print(f"✅ generate_structured: {result}")

if __name__ == "__main__":
    test_generate_content()
    test_count_tokens()
    test_generate_structured()
    print("\n✅ All Step 1 tests passed!")
