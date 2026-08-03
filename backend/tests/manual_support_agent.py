# 手動実行用スクリプト（pytest の収集対象にしないため test_ で始めない）。
#
#   uv run python -m backend.tests.manual_support_agent
#
# 実 LLM / Qdrant を使うため、ANTHROPIC_API_KEY と Qdrant 起動が前提。
import os

from backend.app.core.support_agent import run_support_agent_core

assert os.getenv("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY が未設定です"

# ② Execute の内部で reasoning まで実行される
result = run_support_agent_core(
    query="住民票の写しの取り方は？",
    vertical="gov",          # → prompt_addendum が reasoning に注入される
)
print('\n------------------')
print(result.answer)         # reasoning が生成した最終回答
print('------------------')
