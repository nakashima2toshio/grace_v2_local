---
name: grace-agent-tests
description: >-
  Fix and maintain the pytest suite AND author test documentation in grace_v2
  (and the sibling *_grace_agent repos). Use when `uv run pytest` reports
  collection errors, failures, or warnings, when adding/guarding tests, or when
  writing/updating test-spec docs that follow `a_test_md_format.md` (SAE
  format). Encodes grace_v2's stub-based backend suite, the CI gate contract,
  the common test-debt patterns, the SAE test-doc format, and how to verify
  with uv.
---

# grace_agent テスト保守スキル

pytest の失敗・収集エラー・警告を直すための知見と、テスト仕様書
（`a_test_md_format.md`・SAE形式）の作成知見。
原則 **テストのみ修正**（本番コードは現行を正とする。疑わしければ報告）。

## 0. このリポジトリ（grace_v2）のテスト構成 — 先に把握する

**テストは `backend/tests/` にある。リポジトリ直下に `tests/` は存在しない。**

```
[tool.pytest.ini_options]
testpaths = ["backend/tests"]     # pyproject.toml
```

| ファイル | 対象 |
|---|---|
| `backend/tests/conftest.py` | 共通スタブ（`PipelineStub` / `StepResultStub` / `GroundednessStub` / `pipeline_stub` fixture） |
| `backend/tests/test_support_agent_core.py` | `run_support_agent_core` の配線（ゲート・HITL・Web フォールバック） |
| `backend/tests/test_api.py` | FastAPI ルート（`/api/support/*`, `/api/verticals`, `/api/health`） |
| `backend/tests/test_intervention_bridge.py` | HITL ブリッジ |
| `backend/tests/test_groundedness_sources.py` | P-01: groundedness へ渡す出典**本文** |
| `backend/tests/test_similarity_selection.py` | P-04: コサイン類似度の二段構え選抜 |
| `backend/tests/test_collection_selection.py` | P-04 回帰 + P-03: コレクション探索順・緩和結果の保留 |
| `backend/tests/manual_support_agent.py` | **手動実行専用**（実 API キー必須）。`test_` で始めないこと（pytest に収集されると import 時 `AssertionError: ANTHROPIC_API_KEY` で**全テストが収集エラーになる**） |

### スタブ設計（実 API キー・Qdrant 不要）
- `install_pipeline_stub()` が `backend.app.core.support_agent` の外部依存
  （`get_config` / `create_planner` / `create_executor` / `create_groundedness_verifier` /
  `create_source_agreement_calculator` / `create_tool_registry` / `create_intent_classifier` /
  `create_no_info_judge`）を `monkeypatch.setattr` で丸ごと差し替える。
- スタブは**遅延評価**（実行時に `stub` の属性を読む）。設置後にテスト側で
  `stub.answer` / `stub.sources` / `stub.source_texts` / `stub.groundedness` を
  書き換えればシナリオを変えられる。
- `stub.verify_calls` に `verifier.verify(...)` へ渡された sources が積まれる →
  「本文が渡ったか / 出典ラベルへ fallback したか」を assert できる。
- RAG 系テストは Qdrant を避けるため `RAGSearchTool.__new__(RAGSearchTool)` で
  `__init__` を回避し、`agent_tools.search_rag_knowledge_base_structured` を
  monkeypatch する（`grace/tools.py` が関数内で遅延 import しているため差し替え可能）。
- conftest の import は **`from backend.tests.conftest import ...`**（bare `from conftest import ...` は
  `ModuleNotFoundError`）。

## 1. 実行・検証

```bash
uv run pytest backend/tests -q          # 全体（現状 64 passed / 約 30 秒）
uv run pytest backend/tests/test_api.py -q
uv run ruff check .                     # ブロッキングCIゲート
```

- `pyproject.toml` に `pythonpath` 指定は無い。CI は `PYTHONPATH=.` を env で与えている。
  ローカルで直接 `python backend/tests/x.py` を叩くとスクリプト位置が sys.path に入り
  `ModuleNotFoundError: No module named 'backend'` になる → `uv run python -m backend.tests.x` を使う。
- 修正は**本体で 1 ファイルずつ順に処理する**のが既定。サブエージェントは呼び出し元の
  文脈を引き継がず毎回ゼロから調べ直すため割高で、**ユーザーが並列実行を明示的に指示した
  場合のみ**ファイル単位で起動する（各自 `uv run pytest <file> -q` で 0 failed/0 error を確認）。
- ruff はブロッキングCIゲート → 触ったファイルは `ruff check <file>` を必ず通す。
  特に **I001（import 順）** はローカル緑・CI 赤になりやすい（`grace-agent-ci` スキル参照）。

## 2. grace_v2 で実際に踏んだ落とし穴

1. **手動スクリプトが `test_*.py` 命名 → 全テスト収集エラー**
   実 API キーを要求するスクリプトを `backend/tests/test_backend.py` に置いた結果、
   pytest が import した時点で `AssertionError` → `Interrupted: 1 error during collection`
   → 37 テスト全 skip。**手動用は `manual_*.py` に改名する。**
2. **monkeypatch のターゲット名が実在しない**
   存在しないメソッド名を `monkeypatch.setattr` しても（`raising` 既定 True でも
   `SimpleNamespace` 相手だと）気づきにくく、後段で `AttributeError` になる。
   patch 前に **実コードで名前を grep して確認する**（例: `_get_search_candidates` は
   存在せず、正しくは `_get_all_collections_dynamic`）。
3. **回帰テストは「旧コードで落ちること」を確認する**
   修正と同時に書いたテストは、修正前のコードに当てて **fail することを必ず検証**する。
   fail しないなら回帰を捕まえられていない。
4. **config 由来の数値を MagicMock のままにしない**
   `config.qdrant.rag_sufficient_score` / `vectors.size` などは `>` / `>=` 比較に入るため、
   実 float / int を設定しないと `TypeError`。

## 3. 統合テストは「未起動でskip」
- Qdrant: `socket` で `QDRANT_HOST`/`QDRANT_PORT`（既定 localhost:6333）に短 timeout 接続
  できなければ `pytest.mark.skipif` でモジュールごと skip。
- 実 API: `skipif(not os.getenv("ANTHROPIC_API_KEY"/"GOOGLE_API_KEY"))`。
  ユニットは可能なら mock 化を優先（`backend/tests/` は全てスタブベースで実キー不要）。

## 4. 参考: 旧 `*_grace_agent` リポジトリの移行負債

> grace_v2 の `backend/tests/` には**ほぼ当てはまらない**。
> `anthropic_grace_agent_v2` / `openai_grace_agent` / `ollama_grace_agent_v2` 等の
> `tests/` を触るときのみ参照する。

1. **収集エラー（ImportError）= import パス誤り**
   - パッケージ修飾を使う: `helper.helper_llm` / `helper.helper_embedding` /
     `services.qdrant_service` / `qa_qdrant.make_qa_register_qdrant`。
   - 削除済みモジュール（`qa_generation.{content,generation,keyword_extraction,structure}`、
     `register_qdrant`）参照のテストは**廃止＝削除**（要・削除確認）。
2. **旧 patch ターゲット（移行残骸）**
   - `google.generativeai`（旧SDK・未インストール）→ 新SDK `google.genai`。
     helper_llm はモジュール直下 `genai` を持つので `helper.helper_llm.genai` を patch。
   - `services.agent_service.genai`/`.QdrantClient` は廃止。現行は
     `create_llm_client("anthropic")`（`agent.llm`）・`get_qdrant_client()`・
     tool は `search_rag_knowledge_base_cached`。LLM 応答は
     `ToolUseResponse(text, tool_calls, stop_reason, assistant_message)`。
3. **既定値ドリフト（期待値を現行へ）**
   - モデル既定 `gemini-2.0-flash` → `claude-sonnet-4-6`。
   - `config_service`: env override は `ANTHROPIC_API_KEY` → `api.anthropic_api_key`。
   - ValueError メッセージ `"ANTHROPIC_API_KEY is not set"`。
4. **削除された挙動**
   - `smart_qa_generator` の2段階フォールバック廃止 → 構造化失敗時は `success=False`/空。
   - `map_collection_to_csv` は完全一致のみ（`qa_` prefix strip 廃止 → 無ければ None）。
5. **Executor（実LLM呼び出しを mock）**
   - `_is_search_result_sufficient` → True で動的フォールバック連鎖
     （web_search/ask_user 挿入 → partial 化・step 数増）を抑止。
   - `_llm_calculate_step_confidence` / `_calculate_overall_confidence`（`evaluate_final`）も patch。

## 5. その他
- 欠落フィクスチャ → 対象ディレクトリの `conftest.py` に追記
  （複数タスクで同じ conftest を触るなら read-first で追記、clobber 禁止）。
- `Test*` 命名のヘルパークラス（`__init__` あり）→ `__test__ = False` で
  `PytestCollectionWarning` 解消。

## 6. テスト仕様書の作成（`a_test_md_format.md`・SAE形式）

テストファイル（`backend/tests/test_*.py` 等）のドキュメントを書く/最新化するときは、スキル同梱
`.claude/skills/grace-agent-tests/a_test_md_format.md` に従う。モジュール仕様（IPO）とは**観点・構成が異なる**ので混同しない。

- 中心構造は **SAE（Setup-Action-Expected）**＝「準備→実行→検証」。IPO詳細・戻り値例・使用例ワークフローは**使わない**。
- タイトル: `# test_<module>.py - <対象説明> 単体テスト ドキュメント` → `**Version X.X** | 最終更新: YYYY-MM-DD`。
- 必須セクション順:
  1. 目次
  2. 概要（**テーブル**＝テストファイル/テスト対象/対象クラス/対象メソッド/フレームワーク(`pytest + unittest.mock`)/関連ファイル ＋ `### テスト方針`）
  3. テスト対象の責務と境界（`### 責務` 箇条書き ／ `### テスト対象外` 表＝対象外処理・責務モジュール・理由 ／ 責務境界図(Mermaid)）
  4. テスト構成図（テストクラス構成図 ＋ 主要メソッドの処理フロー図、いずれも Mermaid）
  5. モック・フィクスチャ設計（**モックする**表＝対象/パッチパス/内容/理由 ＋ **モックしない**表 ／ フィクスチャ一覧＋詳細 ／ テストデータ ／ ヘルパー関数）
  6. テストケース一覧（クラスごとの表＝`ID`/`テスト名`/`分類`(正常・異常・境界)/`検証内容` ＋ **カバレッジマトリクス**＝メソッド×分類の件数）
  7. テストケース詳細（各ケースを **SAEテーブル**＝Setup/Action/Expected。`> 📝 **根拠**: <file> L番号` を付ける。`parametrize` は**パラメータ一覧表**を併記）
  8. 実行方法（`pytest ...` コマンド ／ 環境要件表 ／ 注意事項）
  9. 変更履歴
- SAE 記法: **Action** は呼び出しコードを1行（`result = planner.estimate_complexity(query)`）、**Expected** は `assert ...`（複数は `<br>` 改行、近似は `pytest.approx`）。
- Mermaid は本リポジトリ共通の**黒背景・白文字**（`classDef default fill:#000,stroke:#fff,color:#fff` を末尾、全ノードに `class ... default`、全サブグラフに `style ... fill:#1a1a1a`）。スコープ境界の色分けは `stroke` 色のみ変更し背景 `#1a1a1a` は維持（対象内 `stroke:#4CAF50` / 対象外 `stroke:#E91E63` 等）。sequenceDiagram は先頭に `%%{ init: ... }%%`。
- 所在: テスト仕様書は対象テストに対応するドキュメント領域（grace_v2 は `backend/docs/`）。既存配置に従う。
- 整合: **実テストコードを読んで**、テストクラス/ケース名・件数・patch パス・フィクスチャ・カバレッジ件数を突合する（数を盛らない）。
- 一括作成も**既定は本体で順に処理**。**ユーザーが並列実行を明示的に指示した場合のみ**
  ファイル単位でサブエージェントを起動し、各に `a_test_md_format.md` パス＋対象テスト＋
  黒背景Mermaid規約を渡す。仕上げに mermaid 準拠を grep 検証。
