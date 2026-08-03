# grace_v2_local: Anthropic → Ollama（ローカル LLM）移植インベントリ

**対象リポジトリ**: `grace_v2_local`
**移植元**: Anthropic Claude（LLM）＋ Gemini Embedding
**移植先**: Ollama（ローカル LLM、OpenAI 互換 API）
**参照実装**: `ollama_grace_agent_v2`
**参照仕様**: `migration_openai2ollama.md`（添付資料）
**作成日**: 2026-08-03

## 決定事項（2026-08-03）

| # | 論点 | 決定 |
|---|---|---|
| 1 | Embedding のローカル化 | **(a) LLM だけ Ollama、Embedding は Gemini 継続。** `gemini-embedding-001`(3072) と `GOOGLE_API_KEY` は維持し、**Qdrant コレクションは再作成しない**（コレクション名 `*_anthropic` もそのまま） |
| 2 | ReAct 戻り値の型不一致 | **改修する**（→ 実施済み。§6 参照） |
| 3 | `max_output_tokens: 10` の判定系 5 箇所 | **課題・宿題として残す**（→ §7 参照） |
| 4 | API キーの起動ガード | **Embedding 以外は削除する**（→ 実施済み。§6 参照） |
| 5 | `float(text)` 直変換 3 箇所 | **regex 抽出へ改修する**（→ 実施済み。§6 参照） |

> 本書の §1〜§5 は「何を移植する必要があるか」の一覧。§6 に実施済みの内容、
> §7 に意図的に残した宿題を記す。

---

## 0. 前提と、参照実装との差分

`ollama_grace_agent_v2` は `helper/helper_llm.py`・`helper/helper_embedding.py`・
`grace/llm_compat.py`・`grace/*` のレイヤまでは grace_v2_local とほぼ同じ構造で、
**そのまま持ってこられる部分が多い**。一方、grace_v2_local にしか無い層があり、
そこは参照実装が存在しないため自前で対応する必要がある。

| 層 | 参照実装（ollama_grace_agent_v2） | grace_v2_local |
|---|---|---|
| `helper/helper_llm.py` | `OllamaClient` あり（1035行） | Anthropic/OpenAI/Gemini のみ（428行）→ **移植** |
| `helper/helper_embedding.py` | `OllamaEmbedding` あり | Gemini/OpenAI/FastEmbed のみ → **移植** |
| `grace/llm_compat.py` | `OllamaGenaiClient` あり（235行） | `AnthropicGenaiClient`（281行、thinking 制御あり）→ **移植＋差分吸収** |
| `grace/planner|executor|confidence|tools|replan` | 移植済 | `create_chat_client` 経由 → **llm_compat 差し替えでほぼ吸収** |
| `backend/`（FastAPI + SSE） | **無い** | あり → **自前対応** |
| `frontend/`（Vite+React） | **無い** | あり → **自前対応** |
| `backend/app/core/verticals.py` / `rulesets.py` | verticals のみ別実装 | あり → **コレクション名・INTENT_MODEL 対応** |
| `grace/step_trace/` | **無い** | あり → **文言のみ** |
| `grace/calibration.py` / `heavy_model` / thinking budget | **無い** | あり → **Ollama では無効化の設計判断が必要** |

---

## 1. 移植対象ファイル一覧（Phase 別）

状態凡例: 🔴 実装変更必須 / 🟡 既定値・文言のみ / 🟢 変更不要（確認のみ）

### Phase 1 — 基盤レイヤ（ここを直すと大半が追随する）

| # | ファイル | 種別 | 変更内容 |
|---|---|---|---|
| 1 | `helper/helper_llm.py` | 🔴 クラス追加 | `OllamaClient` 追加、`_resolve_schema_refs()` 追加、`_parse_text_tool_calls()` 追加、`create_llm_client` に `"ollama"` 分岐、`DEFAULT_LLM_PROVIDER="ollama"`、`LLM_MODELS`/`LLM_PRICING`（0 コスト）/`LLM_LIMITS` に Ollama 系追加 |
| 2 | `helper/helper_embedding.py` | 🔴 クラス追加 | `OllamaEmbedding` 追加、`DEFAULT_OLLAMA_EMBEDDING_DIMS=768`、`create_embedding_client` に `"ollama"` 分岐、`DEFAULT_EMBEDDING_PROVIDER`、`get_embedding_dimensions()` |
| 3 | `grace/llm_compat.py` | 🔴 全面差し替え | `AnthropicGenaiClient` → `OllamaGenaiClient`（genai 互換 `.models.generate_content`）、`DEFAULT_OLLAMA_MODEL`、`_extract_config` から `thinking_budget_tokens` 経路の扱いを決定（下記 §4-C） |
| 4 | `grace/config.py` | 🔴 既定値 | `LLMConfig.provider="ollama"` / `model` / `light_model` / `heavy_model` / `EmbeddingConfig.provider,model,dimensions` / `heavy_thinking_budget_tokens` の扱い |
| 5 | `config/grace_config.yml` | 🔴 設定 | `llm.*` / `embedding.*` を Ollama へ。`ollama:` セクション新設（base_url / llm_model / embedding_model / embedding_dims） |
| 6 | `config.py` | 🔴 設定 | `ModelConfig.AVAILABLE_MODELS`/`DEFAULT_MODEL`/`MODEL_PRICING`/`MODEL_LIMITS`、`QdrantConfig.DEFAULT_VECTOR_SIZE`(3072→768) と `DEFAULT_EMBEDDING_MODEL`、`GeminiConfig`（Embedding 用途）→ `OllamaConfig` 相当＋`MODEL_CONSTRAINTS`/`supports_tool_calls()` 追加、`LLMProviderConfig.DEFAULT_LLM_PROVIDER`/`DEFAULT_EMBEDDING_PROVIDER`、`AgentConfig.RAG_AVAILABLE_COLLECTIONS` |
| 7 | `pyproject.toml` | 🟡 依存/lint | `anthropic` の要否、`google-genai` の要否、`[tool.ruff.lint.isort] known-first-party` はモジュール追加時のみ |

### Phase 2 — GRACE コア（`create_chat_client` 経由なので原則は追随、ただし個別の落とし穴あり）

| # | ファイル | 種別 | 変更内容 |
|---|---|---|---|
| 8 | `grace/planner.py` | 🔴 パース修正 | `float(response.text.strip())`（L584 複雑度推定）→ **regex 抽出**。`complexity_max_output_tokens=10` の見直し（ローカルモデルは前置きを喋る） |
| 9 | `grace/confidence.py` | 🔴 パース修正 | `float(text)` 2 箇所（L431 自己評価 / L711 網羅度）→ **regex 抽出**。groundedness の JSON 生成（L550 付近）は `_resolve_schema_refs` 経由の JSON モードへ |
| 10 | `grace/executor.py` | 🟡 設定/文言 | `_evaluate_rag_relevance()`（L1348）は YES/NO 判定。`max_output_tokens: 256` は維持可。`_relevance_check_model()` の既定解決先が `llm.light_model` である点を Ollama 側に合わせる |
| 11 | `grace/tools.py` | 🟡 | `create_chat_client` 経由。`max_output_tokens: self.config.llm.max_tokens`（L519）の値見直しのみ |
| 12 | `grace/replan.py` | 🟢 間接 | 依存先変更に追随 |
| 13 | `grace/schemas.py` | 🟢 変更不要 | Pydantic 定義のみ。ただし **`$defs`/`$ref` を生むネスト構造**が `_resolve_schema_refs` の対象になる（要確認） |
| 14 | `grace/calibration.py` / `grace/memory.py` | 🟢 | LLM 非依存 |
| 15 | `grace/step_trace/*.py` | 🟡 文言 | ステップ表示中の "Anthropic/Claude/Gemini" 表記の統一（s1〜s9, benchmark） |

### Phase 3 — backend（参照実装なし・自前対応）

| # | ファイル | 種別 | 変更内容 |
|---|---|---|---|
| 16 | `backend/app/core/support_agent.py` | 🔴 起動ガード | L227 `if not os.getenv("ANTHROPIC_API_KEY")` の**エラー分岐を Ollama 疎通確認に置換**（キー不要のため常時エラーになる） |
| 17 | `backend/app/core/review_agent.py` | 🔴 起動ガード | L402 同上 |
| 18 | `backend/app/core/gates.py` | 🔴 出力枠 | L47 / L133 `max_output_tokens: 10` の 2 箇所。ローカルモデルは 10 トークンだと判定文字列が出ない → 増枠＋regex/部分一致判定へ |
| 19 | `backend/app/core/review_gates.py` | 🔴 出力枠 | L217 / L286 同上（L171 の 512 はそのまま可） |
| 20 | `backend/app/core/verticals.py` | 🔴 モデル/コレクション | L24 `INTENT_MODEL="claude-haiku-4-5-20251001"` → Ollama 軽量モデル。L95/106/115 の `*_anthropic` コレクション名 → `*_ollama` |
| 21 | `backend/app/core/rulesets.py` | 🔴 コレクション | L420 `ec_ad_rules_anthropic` / `ec_policy_anthropic` → `*_ollama` |
| 22 | `backend/app/api/meta.py` | 🔴 ヘルス | `/api/health` の `anthropic_api_key` / `google_api_key` → `ollama_reachable` 等へ。**frontend の型と連動**（§3） |
| 23 | `backend/app/main.py` | 🟡 文言 | docstring の前提条件（API キー → Ollama 起動） |
| 24 | `backend/app/core/intervention_bridge.py` / `jobs.py` / `review_gates` 以外 | 🟢 | LLM 非依存 |

### Phase 4 — services / データ準備

| # | ファイル | 種別 | 変更内容 |
|---|---|---|---|
| 25 | `services/agent_service.py` | 🔴 **ReAct 戻り値の型不一致** | `create_llm_client("anthropic")`→`("ollama")`、既定モデル。**最重要**: 現行は `ToolUseResponse`(NamedTuple) と `stop_reason=="tool_use"`、`assistant_message` を使うが、参照実装の `OllamaClient.generate_with_tools()` は **3-tuple** を返し `finish_reason=="tool_calls"`、`build_tool_result_message()` は **list** を返す。→ §4-A で吸収方針を決める |
| 26 | `services/qdrant_service.py` | 🔴 次元/プロバイダ | `create_embedding_client(provider="gemini")`（L633）、`dims=3072` 既定（L189/207/210/634/665/866/947） |
| 27 | `qdrant_client_wrapper.py` | 🔴 次元/プロバイダ | `DEFAULT_VECTOR_SIZE=3072`（L50）、`embed_texts(provider="gemini")`（L578）、コレクション定義（L111〜）、`task_type="retrieval_query"`（**Ollama は非対応**、L686） |
| 28 | `services/qa_service.py` | 🔴 | `create_llm_client(provider="anthropic")` |
| 29 | `services/token_service.py` | 🟡 コスト | Anthropic/Gemini 単価表 → ローカル実行はコスト 0（トークン集計のみ） |
| 30 | `services/config_service.py` | 🟡 | `GOOGLE_API_KEY` の取り込み（Embedding をローカル化するなら不要） |
| 31 | `helper/helper_api.py` | 🟡 | `create_llm_client("anthropic")` のフォールバック分岐 |
| 32 | `helper/helper_rag_qa.py` | 🔴 | `create_llm_client(provider="anthropic")` / `create_embedding_client(provider="gemini")` / 単価表（L2454 付近） |
| 33 | `chunking/async_api_client.py` | 🔴 | `create_llm_client("anthropic")` 2 箇所、`max_output_tokens=8192` → `max_tokens`、JSON モード |
| 34 | `chunking/csv_text_to_chunks_text_csv.py` | 🟡 | 既定モデル、出力ファイル名の `_anthropic` サフィックス（L19） |
| 35 | `chunking/models.py` / `chunking/prompts.py` | 🟡 | プロンプト内の出力形式を **オブジェクト `{"key":[...]}` でラップ**（配列直要求は破綻する） |
| 36 | `qa_generation/smart_qa_generator.py` | 🔴 | `create_llm_client(provider="anthropic")`、`max_output_tokens=4096`→`max_tokens`、QA 出力を `{"qa_pairs":[...]}` 形式へ |
| 37 | `qa_generation/semantic.py` | 🔴 | LLM/Embedding 両方のプロバイダ |
| 38 | `qa_generation/pipeline.py` | 🔴 | プロバイダ・既定モデル |
| 39 | `qa_qdrant/__init__.py` / `make_qa_register_qdrant.py` / `register_to_qdrant.py` | 🔴 | `GOOGLE_API_KEY` 必須チェック（L169 / L421）、コレクション名 |
| 40 | `celery_tasks.py` / `celery_config.py` | 🟡 | LLM 呼び出し経路とワーカー並列度（レート制限対策 8 → ローカルは GPU 律速） |

### Phase 5 — frontend / テスト / ドキュメント / 運用

| # | 対象 | 種別 | 変更内容 |
|---|---|---|---|
| 41 | `frontend/src/types.ts` ほか | 🔴 型追随 | `/api/health` のレスポンス型を変えるなら**必ず追随**（CI の frontend ゲートで止まる） |
| 42 | `backend/tests/conftest.py` | 🔴 | `monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")`（L77 / L184）→ Ollama 前提のフィクスチャへ |
| 43 | `backend/tests/test_support_agent_core.py` / `test_review_agent_core.py` / `test_api.py` | 🔴 | API キー未設定エラーを検証しているテスト（`delenv` + メッセージ assert）を Ollama 疎通版へ |
| 44 | `backend/tests/test_scope_and_models.py` / `test_levers.py` / `test_review_gates.py` | 🔴 | `create_chat_client` のスタブ差し替え位置とモデル名 assert |
| 45 | `backend/tests/manual_support_agent.py` | 🟡 | `assert os.getenv("ANTHROPIC_API_KEY")` |
| 46 | `CLAUDE.md` | 🔴 | §3 プロバイダ方針・§7.3 技術スタック表記を Ollama に全面書き換え |
| 47 | `README.md` / `run_dev.sh` / `docs/*` | 🟡 | 前提条件（API キー → `ollama serve` + `ollama pull`） |
| 48 | Qdrant コレクション | 🔴 **再作成必須** | Embedding をローカル化する場合 3072 → 768 で完全に不互換 |

---

## 2. 移植が必要なクラス・関数（詳細）

### 2-1. 新規に持ち込むもの（`ollama_grace_agent_v2` からコピー可）

| 名前 | 所在（移植元） | 役割 |
|---|---|---|
| `OllamaClient(LLMClient)` | `helper/helper_llm.py:700` | OpenAI SDK の `base_url` を Ollama に向けた LLM クライアント |
| `OllamaClient.generate_content()` | 同 L727 | `max_completion_tokens`/`max_output_tokens` を **`max_tokens` に自動統一**、`system` / `response_format` kwarg 対応 |
| `OllamaClient.generate_structured()` | 同 L758 | `_resolve_schema_refs()` 適用済みスキーマ＋JSON モード＋`model_validate_json` |
| `OllamaClient.generate_with_tools()` | 同 L810 | OpenAI 互換 `tools` で ReAct。**テキスト形式ツール呼び出しのフォールバック**、空レスポンス時の tools 無し再試行を内蔵 |
| `OllamaClient.build_tool_result_message()` | 同 L935 | `role:"tool"` メッセージ配列を生成 |
| `_resolve_schema_refs(schema)` | 同 L245 | `$defs`/`$ref` を展開。**ローカルモデルはこれが無いとスキーマをオウム返しする** |
| `_parse_text_tool_calls(text)` | 同 L269 | `Action:tool{...}` 等 3 形式のテキストツール呼び出しをパース |
| `OllamaEmbedding(EmbeddingClient)` | `helper/helper_embedding.py:152` | `nomic-embed-text`(768)。`dimensions` パラメータ非対応 |
| `OllamaGenaiClient` / `_OllamaModels` | `grace/llm_compat.py:121,189` | genai 互換 `.models.generate_content()` を提供。**GRACE コアを一切触らずに済む要** |
| `GeminiConfig.MODEL_CONSTRAINTS` / `supports_tool_calls()` | `config.py:487,549` | tool calling 非対応モデル（phi3, gemma2）で `tools` を送らない安全弁 |

### 2-2. 改修が必要な既存クラス・関数（grace_v2_local 側）

| 名前 | 所在 | 内容 |
|---|---|---|
| `create_llm_client()` | `helper/helper_llm.py:397` | `"ollama"` 分岐追加、既定プロバイダ変更 |
| `create_embedding_client()` | `helper/helper_embedding.py:280` | `"ollama"` 分岐追加 |
| `create_chat_client()` | `grace/llm_compat.py:261` | `AnthropicGenaiClient` → `OllamaGenaiClient` |
| `_extract_config()` | `grace/llm_compat.py:85` | `thinking_budget_tokens` の扱い（Ollama に拡張思考は無い） |
| `_thinking_budget()` / thinking kwargs | `grace/llm_compat.py:46,197-205` | **削除または no-op 化** |
| `LLMConfig` / `EmbeddingConfig` | `grace/config.py:56,87` | 既定値 |
| `resolve_heavy_model()` | `grace/config.py:479` | Ollama では上位/標準の 2 層をどう割り当てるか判断が必要 |
| `PlanGenerator.estimate_complexity_with_llm()` | `grace/planner.py:~570` | `float()` 直変換 → regex |
| `ConfidenceCalculator`（自己評価・網羅度） | `grace/confidence.py:431,711` | `float()` 直変換 → regex |
| `GroundednessVerifier`（JSON 生成） | `grace/confidence.py:~550` | JSON モード＋フラットスキーマ |
| `ReActAgent`（ツールループ） | `services/agent_service.py:320-510` | §4-A 参照 |
| `_evaluate_rag_relevance()` | `grace/executor.py:1348` | YES/NO 判定の頑健化 |
| `run_support_agent_core()` / `run_review_agent_core()` | `backend/app/core/*.py:227,402` | API キー前提チェックの置換 |
| `health()` | `backend/app/api/meta.py:60` | ヘルス項目の置換 |
| `embed_texts_for_qdrant()` / `embed_query()` | `services/qdrant_service.py:628,853` | provider / dims |
| `_get_embedding_client()` / `embed_texts_unified()` | `qdrant_client_wrapper.py:282,605` | provider / dims / `task_type` 非対応 |

### 2-3. API レベルの対応表（本移植で置き換わる呼び出し）

| Anthropic（現行） | Ollama（移植先） |
|---|---|
| `anthropic.Anthropic(api_key=...)` | `OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")` |
| `client.messages.create(model, max_tokens, messages, system=...)` | `client.chat.completions.create(model, messages, max_tokens=...)` |
| `system=` パラメータ | `messages[0] = {"role":"system", ...}` |
| `"".join(b.text for b in message.content)` | `response.choices[0].message.content` |
| `message.usage.input_tokens / output_tokens` | **無し** → `tiktoken` で近似（コストは 0） |
| `thinking={"type":"enabled","budget_tokens":N}` | **無し** → 削除 |
| `stop_reason == "tool_use"` | `finish_reason == "tool_calls"`（＋テキストパースのフォールバック） |
| tools: `{"name","description","input_schema"}` | `{"type":"function","function":{"name","description","parameters"}}` |
| tool_result: 1 個の user メッセージに集約 | `{"role":"tool","tool_call_id":...}` を **ツールごとに 1 件** |
| Pydantic schema をそのまま埋め込み | `_resolve_schema_refs()` でフラット化必須 |
| Gemini `embed_content(output_dimensionality=3072, task_type=...)` | `client.embeddings.create(model="nomic-embed-text", input=...)`（768固定・`task_type` 無し） |
| `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | **不要**。`OLLAMA_BASE_URL` のみ（既定値あり） |

---

## 3. とくに壊れやすい箇所（設計判断が必要）

### A. ReAct ループの戻り値インターフェース不一致 ★最重要
`services/agent_service.py` は `helper_llm.ToolUseResponse`（NamedTuple: `text` /
`tool_calls` / `stop_reason` / `assistant_message`）を前提に書かれており、
`stop_reason != "tool_use"` でループを抜け、`result.assistant_message` を
`self._messages` に追記する。
一方、参照実装の `OllamaClient.generate_with_tools()` は **`(text, tool_calls, finish_reason)`
の 3-tuple** を返し、`build_tool_result_message()` は **list** を返す。

→ **推奨**: grace_v2_local 側の `OllamaClient` は `ToolUseResponse` を返すように
adapt する（`stop_reason` に `"tool_use"` を正規化、`assistant_message` を
OpenAI 形式の `{"role":"assistant","content":..., "tool_calls":[...]}` で構築）。
これで `agent_service.py` の本体ロジックを触らずに済む。ただし
`build_tool_result_message()` が **複数メッセージ**を返す点だけは呼び出し側の
`append` → `extend` 変更が必要。

### B. `max_output_tokens: 10` の判定系（計 5 箇所）
`backend/app/core/gates.py:47,133`、`review_gates.py:217,286`、
`grace/config.py:complexity_max_output_tokens=10`。
Claude は 10 トークンで `YES`/`0.8` を返すが、ローカルモデルは
「はい、判定すると…」と前置きを喋るため **10 トークンでは判定文字が出ない**。
→ 増枠（64〜128）＋ 出力から regex/部分一致で抽出する方式に変更。

### C. 拡張思考（thinking budget）と heavy_model の扱い
`grace/config.py` の `heavy_model` / `heavy_thinking_budget_tokens`、
`grace/llm_compat.py` の thinking 明示制御は **Anthropic 固有**。
Ollama には対応概念が無い。
→ 「設定は残すが Ollama では無視（no-op）」か「削除」かを決める。
設定を残す方が grace_v2（Anthropic 版）との差分が小さく、逆移植も容易。

### D. 数値パース 3 箇所
`grace/planner.py:584`、`grace/confidence.py:431,711` の `float(text)` は
`"答えは 0.8 です。"` で `ValueError`。
→ `re.search(r"[01]?\.\d+|\b[01]\b", text)` で抽出（仕様書 1-11 節）。

### E. JSON 配列を要求しているプロンプト
`response_format={"type":"json_object"}` は**オブジェクトのみ**強制する。
Q&A 生成・チャンク分割で `[{...}]` を直接要求している箇所は
`{"qa_pairs":[...]}` 形式にラップし直す（仕様書 1-5 節）。

### F. Embedding をローカル化する場合の破壊的影響
`gemini-embedding-001`(3072) → `nomic-embed-text`(768) は**次元が違うため
Qdrant コレクションの完全再作成が必須**。加えて:
- コレクション名規約 `*_anthropic` → `*_ollama`（`verticals.py` / `rulesets.py` /
  `config.py::AgentConfig` / `qdrant_client_wrapper.py`）
- Gemini 固有の `task_type="retrieval_query"` / `output_dimensionality` は Ollama 非対応

### G. CI 4 ゲート
`ruff` / `pytest backend/tests` / `compileall` / `frontend(tsc+vitest+build)`。
`/api/health` のレスポンス形を変えると frontend 型が落ちるため、
`frontend/src/types.ts` の追随が必須。

---

## 4. 未決事項（実装前に確認したい）

| # | 論点 | 選択肢 |
|---|---|---|
| 1 | **Embedding もローカル化するか** | (a) LLM だけ Ollama、Embedding は Gemini 継続（`GOOGLE_API_KEY` 必要・**Qdrant 再構築不要**） / (b) Embedding も `nomic-embed-text` 768（完全ローカル・**Qdrant 全再作成＋コレクション改名**） |
| 2 | **既定モデル** | `gemma4:e4b`（参照実装の既定）/ `qwen2.5:7b`（日本語が優秀）/ `llama3.1:8b` |
| 3 | **light / heavy の 2 層** | 現行は `light_model`(haiku) と `heavy_model`。Ollama で 2 モデルを使い分けるか、単一モデルに寄せるか |
| 4 | **thinking budget 設定の残置** | no-op で残す / 設定ごと削除 |
| 5 | **Anthropic 実装の残置** | `AnthropicClient` / `AnthropicGenaiClient` を後方互換として残すか、削除するか |
| 6 | **コレクション名** | `*_anthropic` → `*_ollama` に改名するか、名前は維持するか |

---

## 5. 動作確認手順（移植後）

```bash
# 1. Ollama
ollama serve
ollama pull gemma4:e4b
ollama pull nomic-embed-text        # Embedding もローカル化する場合

# 2. Qdrant（Embedding 変更時は再登録）
docker-compose -f docker-compose/docker-compose.yml up -d
python qa_qdrant/make_qa_register_qdrant.py --recreate

# 3. CI と同じ 4 ゲート
uv run ruff check . --no-cache
uv run pytest backend/tests -q
python -m compileall -q -x '\.venv|/\.git/|/logs/' .
cd frontend && npm run lint && npm test && npm run build

# 4. コア疎通（Web/CLI は同じ run_support_agent_core を通る）
uv run python agent_support_example.py --vertical gov -v "住民票の写しの取り方は？"

# 5. プロバイダ残存チェック
grep -rn 'create_llm_client("anthropic")\|create_chat_client\|ANTHROPIC_API_KEY' \
  --include="*.py" . | grep -v '.venv' | grep -v '^\s*#'
```

---

## 6. 実施済みの改修（2026-08-03）

### 6-1. ReAct 戻り値の型不一致（§3-A の解消）

`helper/helper_llm.py` に **`OllamaClient`** を追加した。参照実装
（`ollama_grace_agent_v2`）は 3-tuple を返すが、本リポジトリでは
**`ToolUseResponse`（AnthropicClient と同一の NamedTuple）を返す**ように adapt した。
これにより `services/agent_service.py` の ReAct ループは**無改造で動く**。

吸収した差分:

| 差分 | 吸収方法 |
|---|---|
| `finish_reason=="tool_calls"` vs `stop_reason=="tool_use"` | ツール呼び出しがあれば `"tool_use"`、`finish_reason=="stop"` なら `"end_turn"` へ正規化 |
| 会話履歴が Anthropic ブロック形式 | `_to_openai_messages()` で OpenAI 形式へ変換（`tool_result` ブロック → `role:"tool"` メッセージへ展開）。既に OpenAI 形式のものは素通し |
| ツール定義 `input_schema` | `{"type":"function","function":{...,"parameters":...}}` へ変換 |
| `assistant_message` の再投入 | OpenAI 形式で返し、次ターンの変換器が素通しする形にした |
| 出力上限パラメータ | `max_completion_tokens` / `max_output_tokens` を **`max_tokens`** へ自動統一 |
| 構造化出力の `$defs`/`$ref` | `_resolve_schema_refs()` でフラット化してから JSON モードで送信 |
| ツール呼び出しをテキストで返すモデル | `_parse_text_tool_calls()` で 3 形式をパース |
| tools 指定で空応答になるモデル | tools 無しで 1 度だけ再試行 |

**移植元のバグを 1 件修正した。** `_parse_text_tool_calls()` のフォーマット2
（JSON 辞書形式）は正規表現 `\{[^{}]*"name"...\}` で切り出しており、
ネストした `"parameters": {...}` を含む**実際のツール呼び出しにマッチしない**。
`json.JSONDecoder().raw_decode()` による括弧対応の走査へ置き換えた。

プロバイダーの既定値は**変更していない**（`LLM_PROVIDER` 環境変数で切り替え）。
既定モデルの決定が未了のため、`create_llm_client("ollama")` を有効化するに留めた。

### 6-2. API キーの起動ガード削除

LLM はローカル実行のため API キーが存在せず、従来のガードは**常にエラー**になる。
Embedding（Gemini）用の `GOOGLE_API_KEY` は維持する。

| ファイル | 変更 |
|---|---|
| `backend/app/core/support_agent.py` | `if not os.getenv("ANTHROPIC_API_KEY")` の早期 return を削除（`import os` も不要になり削除） |
| `backend/app/core/review_agent.py` | 同上 |
| `backend/app/api/meta.py` | `/api/health` から `anthropic_api_key` を削除。`google_api_key`（Embedding）は維持 |
| `backend/tests/conftest.py` | 不要になった `monkeypatch.setenv("ANTHROPIC_API_KEY", ...)` を 2 箇所削除 |
| `backend/tests/manual_support_agent.py` | 起動時 assert を削除 |

**frontend への影響なし。** `frontend/src/` は `/api/health` を一切呼んでおらず
（`client.ts` の呼び出し先は verticals / rulesets / support / review のみ）、
`anthropic` / `claude` / `api_key` / `model` への参照も 0 件。型追随は不要だった。

### 6-3. `float(text)` 直変換 → regex 抽出

`grace/llm_compat.py` に **`parse_score()`** を追加し、3 箇所を置き換えた。

| 箇所 | 用途 | 抽出失敗時 |
|---|---|---|
| `grace/planner.py` | 複雑度推定 | ヒューリスティック `estimate_complexity()` へ |
| `grace/confidence.py` | LLM 自己評価 | 0.5 |
| `grace/confidence.py` | クエリ網羅度 | 0.5 |

### 6-4. テスト

| ファイル | 内容 |
|---|---|
| `backend/tests/test_ollama_llm_client.py`（新規・16 件） | ReAct 契約（`ToolUseResponse` / `stop_reason` 正規化 / `assistant_message` の再投入 / ツール形式変換 / `max_tokens`）、テキストツール呼び出しのフォールバック、スキーマ展開、メッセージ変換 |
| `backend/tests/test_parse_score.py`（新規・17 件） | `parse_score()`。`float()` が例外になる入力で `parse_score()` が通ることを明示的に検証 |
| `test_support_agent_core.py` / `test_review_agent_core.py` | 「API キー未設定でエラー」→「API キー無しでも走る」へ反転 |
| `test_api.py` / `test_review_api.py` | ジョブ失敗の誘発方法を「キーを外す」→「明示的に例外を起こす」へ変更 |

**検証結果**（CI と同じ 4 ゲート、いずれもローカル実行）:

```
uv run python -m pytest backend/tests -q   → 366 passed, 1 skipped
uv run ruff check . --no-cache             → All checks passed!
python -m compileall -q ...                → rc=0
frontend: npm run lint / npm test / build  → tsc OK / 62 passed / built
```

---

## 7. 宿題として残した課題

### 7-1. `max_output_tokens: 10` の判定系 5 箇所 ★未着手（意図的）

| ファイル | 行 | 用途 |
|---|---|---|
| `backend/app/core/gates.py` | 47 | 意図分類（question / request / incident） |
| `backend/app/core/gates.py` | 133 | 情報なし回答の検知（answered / no_info） |
| `backend/app/core/review_gates.py` | 217 | 強調表現の分類（claim / negation / quotation） |
| `backend/app/core/review_gates.py` | 286 | 指摘の実質性判定（substantive / vacuous） |
| `grace/config.py` | `complexity_max_output_tokens` | 複雑度推定 |

**調査済みの前提**: 出力のパース側は既に部分一致（`if label in text`）で実装されて
おり頑健である。問題は**出力枠が 10 トークンしかない**点に尽きる。ローカルモデルは
「はい、この問い合わせは question に分類されます」のように前置きを喋るため、
10 トークンで打ち切られると判定語が本文に現れず、全件が安全側
（`None` → キーワード判定 / 強制エスカレ）へ倒れる。

**想定される対処**: 枠を 64〜128 へ広げる。パース側の変更は不要な見込み。
実モデルでの挙動を見てから決めるのが妥当なため、既定モデル確定後に着手する。

### 7-2. 未決のままの論点

| # | 論点 | 備考 |
|---|---|---|
| 1 | 既定モデル | `gemma4:e4b` / `qwen2.5:7b`（日本語優秀）/ `llama3.1:8b`。決定後に `config.py` / `grace/config.py` / `config/grace_config.yml` の既定値と `DEFAULT_LLM_PROVIDER` を切り替える |
| 2 | `light_model` / `heavy_model` の 2 層 | Ollama で 2 モデルを使い分けるか単一に寄せるか |
| 3 | thinking budget 設定 | `grace/llm_compat.py` の `_thinking_budget()` と `heavy_thinking_budget_tokens`。Ollama に対応概念なし → no-op 化か削除か |
| 4 | `grace/step_trace/` の `have_key()` | `_trace.py:88` の `ANTHROPIC_API_KEY` 判定は「実呼び出し or 代表サンプル」の切替。起動ガードではないため今回は据え置き。Ollama 疎通確認へ置換する場合、`s2`〜`s8` の 6 ファイルと `docs/` 8 ファイルが追随する |
| 5 | `grace/llm_compat.py` の Ollama 化 | `create_chat_client()` は現状 `AnthropicGenaiClient` のまま。GRACE コア（planner/executor/confidence/tools + backend gates）はすべてここを通るため、**既定モデル確定後の最優先項目** |
| 6 | CI ワークフロー | `.github/` がリポジトリに存在せず、CI が 1 つも走らない。grace_v2 から移植するかは別途判断 |
