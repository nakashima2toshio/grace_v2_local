# テスト実行結果の分析・評価（警告・スキップ・失敗の原因と対策）

`uv run pytest tests/ -v` をローカル（macOS / Python 3.12）で実行した結果
**1 failed / 437 passed / 4 skipped / 20 warnings** に対する原因特定と対策。

## 0. 全体像

| 区分 | 件数 | 性質 | 対応要否 |
|---|---|---|---|
| FAILED | 1 | **テスト分離不備**（ローカルの実APIキー混入）。CI/サンドボックスでは緑 | 要修正（小） |
| SKIPPED | 4 | すべて**意図的スキップ**（削除済み機能・仕様変更の記録） | 不要（任意で整理） |
| WARNINGS | 20 | 2件=命名衝突、18件=モックとSDKの相性（無害） | 任意（ノイズ低減） |

> **重要**: この FAILED は**開発マシン環境固有**。`.env` の実 `GOOGLE_API_KEY`（`AIzaSy…`）が
> 原因で、CI や実キー無しのサンドボックスでは 1 件も落ちず緑になる。

## 1. FAILED（1件）— `tests/services/test_agent_service.py::TestReActAgent::test_init`

**原因（根本）**: 本番コード `services/agent_service.py:_setup_client()` はキーを次の順で解決する。

```python
api_key = get_config("api.google_api_key")          # ← ①最優先（config_service が .env を読む）
if not api_key:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")  # ②フォールバック
```

テストは `os.environ['GEMINI_API_KEY']='test_key'` だけをパッチし、**①の `get_config` を
無効化していない**。開発マシンでは `.env` の実キーが `config_service` 経由で①に入るため、
`test_key` が使われず `AIzaSy…` が `genai.Client` に渡り、
`assert_called_with(api_key='test_key')` が失敗する。

**対策**: 同ファイルの `test_init_missing_key` が既に行っている手法（`get_config` をモックして
`api.google_api_key` を `None` にする）を `test_init` にも適用し、②のフォールバック経路を
確定的に通す。

```python
def test_init(self, mock_genai):
    with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}, clear=True), \
         patch("services.agent_service.get_config") as mock_get_config:
        mock_get_config.side_effect = lambda key, default=None: None if key == "api.google_api_key" else default
        agent = ReActAgent(selected_collections=["coll1"], model_name="gemini-pro")
        ...
        mock_genai.Client.assert_called_with(api_key='test_key')
```

→ `.env` の有無に依らず決定的に通る（テストの環境依存を解消）。

## 2. SKIPPED（4件）— すべて意図的・正常

| # | テスト | スキップ機構 | 原因 | 対策 |
|---|---|---|---|---|
| 1 | `qa_generation/test_generation.py`（モジュール全体・収集時の「1 skipped」） | `pytest.skip(allow_module_level)` | `QAGenerator` API は削除され `SmartQAGenerator` に統合 | 不要。完全に不要なら将来ファイル削除 |
| 2 | `qa_generation/test_content.py::test_analyze_chunk_complexity` | `@pytest.mark.skip` | `analyze_chunk_complexity` は gemini リファクタで削除 | 不要（削除の記録） |
| 3 | `test_metadata_and_full_process.py::test_get_params_prioritizes_payload` | `@unittest.skip` | `get_collection_embedding_params` が payload より**ベクトル次元優先**に仕様変更 | 不要（仕様変更の記録）。整理するなら新仕様向けに書き直し |
| 4 | `test_qdrant_service_metadata.py::test_get_collection_embedding_params_with_payload` | `@unittest.skip` | 同上 | 同上 |

→ いずれも**失敗ではなく設計どおり**。放置して問題なし。気になる場合のみ stale テストを削除/書き換え。

## 3. WARNINGS（20件）— 2種類

### 3-A. 命名衝突（2件）— `tests/test_collection.py:67, :81`

```
PytestCollectionWarning: cannot collect test class 'TestResult'/'TestRunner'
because it has a __init__ constructor
```

**原因**: `TestResult` / `TestRunner` は**ヘルパークラス**だが、名前が `Test…` で始まるため
pytest がテストクラスとして収集しようとし、`__init__` を持つため収集不可の警告。

**対策（どちらか）**:
- 各クラスに `__test__ = False` を付与（最小・推奨）
- もしくは `ResultRecord` / `CollectionRunner` 等へリネーム

### 3-B. MagicMock と google-genai SDK の相性（18件）— `test_execute_turn_with_tool_call`

```
UserWarning: <MagicMock ...> is not a valid ApiSpec / HttpElementLocation / AuthType / ...
（google/genai/_common.py:651）
```

**原因**: ツール定義（function declaration）に `MagicMock` を渡しているため、SDK が tool 仕様の
enum 型フィールドを走査するたびに、MagicMock の自動生成属性が enum 値として不正で
**1フィールド=1警告**を出す（9種 × 2ツール ≒ 18件）。**テスト自体は PASS** で、機能には無害。

**対策（いずれか）**:
1. ノイズ抑制のみでよい → `pyproject.toml` の pytest 設定でフィルタ:
   ```toml
   [tool.pytest.ini_options]
   filterwarnings = ["ignore:.*is not a valid.*:UserWarning:google.genai._common"]
   ```
2. 根本対処 → ツール spec を実体（`types.FunctionDeclaration` 等）か `MagicMock(spec=...)` に
   する、または SDK がモックを内省しない上位境界でモックする（手間大）。

## 4. 推奨対応順（コスト/効果）

| 優先 | 項目 | 対応 | 効果 |
|---|---|---|---|
| ★高 | FAILED `test_init` | `get_config` をモック（§1） | 環境依存の失敗を解消し常時緑 |
| 中 | 警告2件（命名衝突） | `__test__ = False`（§3-A） | 警告 -2、収集の混乱解消 |
| 低 | 警告18件（genai） | `filterwarnings` で抑制（§3-B） | ログのノイズ大幅減 |
| 任意 | SKIP 4件 | 放置 or stale 削除 | 整理目的のみ |

---

# テストスイート索引（gemini_grace_agent）

日本語 RAG Q&A システムの pytest テスト群。LLM は **Gemini**（デフォルト
`gemini-2.5-flash`）、Embedding は **Gemini `gemini-embedding-001`（3072次元）** を前提とする。

## 実行方法

```bash
# 全テスト
uv run pytest tests/

# ディレクトリ単位
uv run pytest tests/qa_generation/ -v
uv run pytest tests/grace/ -v
uv run pytest tests/services/ -v
uv run pytest tests/helpers/ -v

# 単一ファイル / 単一テスト
uv run pytest tests/helpers/test_helper_llm.py -v
uv run pytest tests/services/test_qdrant_service.py::TestContentBasedPointId -v
uv run pytest tests/qa_generation/test_pipeline_persistence.py -v
```

> 依存: `pytest`, `pydantic`, `numpy`, `pandas`, `tiktoken`, `google-genai`,
> `qdrant-client`。大半のテストは Gemini SDK（`google.genai`）/ Qdrant を
> `unittest.mock` でモックするため、API キーや稼働中の Qdrant なしで実行できる。

## ディレクトリ構成

| ディレクトリ | 内容 |
|---|---|
| `tests/grace/` | GRACE 自律エージェント（Planner + Executor、confidence、replan、intervention、schemas、config）の単体・統合テスト |
| `tests/services/` | サービス層（cache / config / dataset / file / json / log / qa / token / agent / qdrant）の単体テスト |
| `tests/qa_generation/` | Q/A 生成パイプライン（semantic / evaluation / structure / keyword / SmartQAGenerator 逐次永続化）のテスト |
| `tests/chunking/` | ドキュメントチャンキングのテスト（最大トークン強制分割） |
| `tests/helpers/` | プロバイダー抽象化レイヤー（`helper/helper_llm.py` / `helper/helper_embedding.py`）の Gemini / OpenAI クライアントテスト |
| `tests/agents/` | エージェントの実 API 結合テスト（`GEMINI_API_KEY` 必須・未設定時スキップ） |
| `tests/legacy/` | 旧構成を対象としたレガシーテスト（`conftest.py` が `temp_dir` フィクスチャを提供） |
| `tests/*.py` | トップレベル: 結合・回帰テスト（下表参照） |
| `tests/conftest.py` | `sys.path` 補完（プロジェクトルート + `helper/`）と、テスト件数のカスタム出力フック |

### `tests/qa_generation/`

| ファイル | 対象 / 備考 |
|---|---|
| `test_semantic.py` | `qa_generation.semantic.SemanticCoverage`。埋め込み次元 3072・Gemini Embedding を検証。クライアントは全モック |
| `test_evaluation.py` | `qa_generation.evaluation.analyze_coverage`。`SemanticCoverage` をモックするためプロバイダー非依存 |
| `test_structure.py` | `helper.helper_text.merge_small_chunks` 等のチャンク整形 |
| `test_keyword_extraction.py` | キーワード抽出ユーティリティ |
| `test_pipeline_persistence.py` | `QAPipeline` の **チャンク単位 JSONL 逐次永続化・クラッシュ再開**（処理済み skip・壊れ行スキップ・clear）。`SmartQAGenerator` はモック |
| `test_content.py` | 一部 `@pytest.mark.skip`（`analyze_chunk_complexity` は gemini リファクタで削除済み） |
| `test_generation.py` | モジュール冒頭で `pytest.skip`（`QAGenerator` API は `SmartQAGenerator` に置換され削除済み） |

### `tests/services/`

| ファイル | 対象 / 備考 |
|---|---|
| `test_qdrant_service.py` | `build_points_for_qdrant` ほか。`TestContentBasedPointId` で **内容ハッシュ point ID**（`stable_point_id` / `_content_point_key`・位置非依存・再登録べき等・provenance）を検証 |
| `test_qa_service.py` / `test_config_service.py` / `test_dataset_service.py` / `test_file_service.py` / `test_json_service.py` / `test_log_service.py` / `test_cache_service.py` / `test_token_service.py` / `test_agent_service.py` | 各サービスの単体テスト。Gemini SDK / Qdrant はモック |

### `tests/helpers/`

| ファイル | 対象 / 備考 |
|---|---|
| `test_helper_llm.py` | `create_llm_client("gemini")` / `GeminiClient`（`generate_content` / `generate_structured` / `count_tokens`）。Gemini SDK はモック。`create_llm_client("openai")` で OpenAI クライアントへの切替も検証 |
| `test_helper_embedding.py` | `create_embedding_client("gemini")` / Gemini Embedding、次元 3072（`gemini-embedding-001`）。`get_embedding_dimensions("gemini") == 3072` |

### トップレベル `tests/*.py`（主なもの）

| ファイル | 対象 / 備考 |
|---|---|
| `test_collect_results.py` | Celery `collect_results` の **完了順回収**（HOLブロッキング解消）・`on_result` / `usage_out` 集約・旧list吸収・タイムアウト。`AsyncResult` をモック（実 Redis 不要） |
| `test_agent_4operations.py` | エージェント4操作の結合テスト |
| `test_qdrant_service_metadata.py` / `test_register_qdrant_metadata.py` / `test_metadata_and_full_process.py` | Qdrant 登録メタデータの round-trip |
| `test_make_qa_register_qdrant_csv*.py` | 登録 CLI の CSV 経路 |
| `test_collection.py` / `test_confidence_fix.py` / `test_dynamic_thresholding.py` / `test_helper_llm_step1.py` | 各種回帰・単体 |

## プロバイダー適応メモ（Gemini）

このリポジトリは anthropic_grace_agent からの移植であり、テストは以下の Gemini 値に
読み替えてある。

| 項目 | 値 |
|---|---|
| LLM プロバイダー / デフォルトモデル | `"gemini"` / `gemini-2.5-flash` |
| LLM クライアント生成 | `create_llm_client("gemini")` |
| 構造化出力 | `client.models.generate_content(response_schema=...)`（`SmartQAGenerator` は構造化出力 **1回**で分析＋生成） |
| Embedding モデル / 次元 | `gemini-embedding-001` / 3072 |
| Embedding クライアント生成 | `create_embedding_client("gemini")` |
| API キー | `GOOGLE_API_KEY` / `GEMINI_API_KEY` |
| Qdrant コレクション | `*_gemini` |
| ポイント ID | 内容ハッシュ（`stable_point_id`、MD5 ベース決定的 63bit・位置非依存） |

### 移植時にスキップ / 書き換えたテスト

anthropic 版に存在したが、gemini では対象モジュール / 実装が異なるため移植していない:

- `test_content.py` の `analyze_chunk_complexity`、`test_generation.py` の `QAGenerator`:
  gemini の `qa_generation/` には存在しない（`SmartQAGenerator` に統合）ため skip。
- `SmartQAGenerator` は anthropic の Tool Use 構造化出力ではなく
  `google.genai` の `generate_content(response_schema=...)` を用いる **単段** 実装に書き換え。

## 環境変数でゲートされるテスト

| ゲート | 対象 | 挙動 |
|---|---|---|
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` 未設定 | `tests/grace/test_planner_integration.py`, `tests/grace/test_executor_integration.py`, `tests/test_helper_llm_step1.py`, `tests/agents/test_agent_service_paris_income.py` | `@pytest.mark.skipif` 等で実 Gemini API テストをスキップ |
| 実 Qdrant 稼働 | grace 統合 / 登録系で実 Qdrant を参照するテスト | Qdrant 未稼働時はスキップ / モックで代替 |

> `tests/grace/conftest.py` は `GOOGLE_API_KEY` をプレースホルダ `"test-api-key"` で
> `setdefault` し、`LLM_PROVIDER` / `EMBEDDING_PROVIDER` を `gemini` に設定する。
> このプレースホルダはモックテストの import 充足用であり、実 API テストは
> 上記ゲートで別途スキップ判定される。
> `tests/helpers/` と `tests/qa_generation/` の通常テストはすべて SDK をモックするため、
> API キー・Qdrant なしで完走する。
