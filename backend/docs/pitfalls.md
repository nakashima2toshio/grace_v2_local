# backend の落とし穴 ドキュメント

**Version 1.0** | 最終更新: 2026-09-16

> **本書の位置づけ**: **コードを触る前に読む 1 枚。** 各モジュール文書に散らすと
> 「踏んでから読む」ことになる知識——非自明な設計判断・過去に実際に壊れた箇所・
> 直したくなるが直してはいけない箇所——を集めた。**ローカル LLM 固有の罠を含む。**

> **関連ドキュメント**
> - [`architecture.md`](./architecture.md) / [`job_runtime.md`](./job_runtime.md)
> - [`config_and_providers.md`](./config_and_providers.md)
> - `CLAUDE.md` の CRITICAL RULES（R1〜R5）が上位規範

---

## 目次

- [1. Support と Review は部品を共用している](#1-support-と-review-は部品を共用している)
- [2. import の副作用で runner が登録される](#2-import-の副作用で-runner-が登録される)
- [3. ログ転送はスレッドで絞っている](#3-ログ転送はスレッドで絞っている)
- [4. HITL のタイムアウトは「実行しない」](#4-hitl-のタイムアウトは実行しない)
- [5. モデル解決は定数を直接参照しない](#5-モデル解決は定数を直接参照しない)
- [6. ローカル LLM（Ollama）固有の罠](#6-ローカル-llmollama固有の罠)
- [7. Embedding を Ollama にしない](#7-embedding-を-ollama-にしない)
- [8. ステップ番号は実行順と一致しない](#8-ステップ番号は実行順と一致しない)
- [9. API スキーマを変えたら types.ts も変える](#9-api-スキーマを変えたら-typests-も変える)
- [10. 直してはいけないもの](#10-直してはいけないもの)
- [11. 変更履歴](#11-変更履歴)

---

## 1. Support と Review は部品を共用している

**Support のつもりで入れた変更が Review を壊す。**

| 共用部品 | 実体 |
|---|---|
| `GroundednessVerifier` | `grace.confidence.create_groundedness_verifier` |
| `InterventionBridge` | `backend/app/core/intervention_bridge.py` |
| `ActionBackend` / 本人確認 | `support_actions.py` |
| ジョブ基盤・SSE | `backend/app/core/jobs.py` |
| キーワード一致・モデル解決 | `gates.py::_match_keyword` / `judge_model`（`review_gates.py` が再利用） |

`backend/tests` の `test_*.py` 92 ファイル中 **18 ファイルが Review 系**である。
**共用部品を触ったら `backend/tests/test_review_*.py` も流す。**

---

## 2. import の副作用で runner が登録される

`review_agent.py` / `data_jobs.py` は **import 時に** `register_runner()` を実行する。

- **消してはいけない**: 「使われていない import」に見えても、消すと
  `_resolve_runner` が `TypeError: 未登録の params 型です` を出す
- **循環 import を作らない**: `jobs.py` から Review / データ準備を import してはいけない

詳細: [`job_runtime.md` §3](./job_runtime.md)

---

## 3. ログ転送はスレッドで絞っている

`capture_logs()` が付ける `JobLogHandler` は**プロセス全体のロガー**に付く。
そのため 2 つの守りが入っている。**どちらも消すと実測で壊れる。**

| 守り | 消すとどうなるか |
|---|---|
| `record.thread != self._thread_ident` で無視 | 同時実行した別ジョブのログが混ざる |
| ロガー level の参照カウント（`_level_refs`） | 2 本同時実行後、ロガーが INFO のまま復元されない |

`capture_logs` は**必ず `with` で使う**。外し忘れるとハンドラが積み上がり、
1 行のログが N 回転送される。

---

## 4. HITL のタイムアウトは「実行しない」

`InterventionBridge.resolver()` は時間切れ（既定 300 秒）で
`InterventionResponse(action=CANCEL, timeout_reached=True)` を返す。

- **「返事が無い＝承認」にしない。** 破壊的操作（コレクション削除・アクション実行）が無人で走る
- **CLI の自動承認（`AUTO_PROCEED`）を Web 経路に持ち込まない**
- タイムアウトしても**ジョブは失敗しない**。安全側に倒して続行する

---

## 5. モデル解決は定数を直接参照しない

`INTENT_MODEL` / `get_default_ollama_model()` を直接使わず、
`judge_model(config)` / `detect_model(config)` / `_resolve_model(explicit)` を経由する。

過去の実測では、判定系だけが別モデル（`num_ctx` 4096 の派生元）で動いて**空応答**になり、
データジョブだけが環境変数を見て「**ヘッダーは A・実行は B**」になった。
詳細: [`config_and_providers.md` §2](./config_and_providers.md)

### ⚠️ 既定値を dataclass のデフォルトで評価しない

```python
# ❌ import 時に 1 度だけ確定してしまう
model: str = get_default_ollama_model()

# ✅ None のまま持ち回り、_resolve_model() の 1 箇所で解決する
model: Optional[str] = None
```

前者だと `.env` と `grace_config.yml` が食い違ったときに気づけない。

---

## 6. ローカル LLM（Ollama）固有の罠

| 論点 | 対処 |
|---|---|
| **出力上限パラメータ** | **`max_tokens` のみ**。`max_completion_tokens` / `max_output_tokens` は非対応（`OllamaClient` が自動変換する） |
| **構造化出力** | **`response_format={"type":"json_schema"}`（スキーマ制約付きデコード）を使う。** `json_object` は「有効な JSON」しか保証せず、**スキーマ定義そのものをオウム返しされる**（実測: `llama3.2:latest`）。未対応の Ollama では自動で `json_object` へ落ち、その場合は `SchemaEchoError` が名指しで検知する |
| **JSON 配列の要求** | `json_object` は**オブジェクトのみ**。`{"key": [...]}` でラップして要求する |
| **数値のみの出力要求** | `float(text)` 直変換は不可。`grace.llm_compat.parse_score()` を使う |
| **拡張思考（thinking）** | **存在しない**。`heavy_thinking_budget_tokens` は設定互換のため残っているが無視される |
| **tool calling** | 非対応モデルへ `tools` を送ると**無応答**になる。`OllamaConfig.supports_tool_calls()` で弾き、セレクタにも出さない |
| **未 pull のモデル** | 実行時 404。**モデル名が間違っているわけではない**（`ollama pull` を先に実行する）。チャンク化は 3 回リトライしてフォールバック分割へ落ちるため、**止まらずにゴミを作り続ける** → `model_not_pulled_message()` で LLM ループ前に弾く |
| **Ollama 未起動** | `ollama_unreachable_message()` で弾く。「一覧が取れない」（判定不能）と「接続拒否」（確実に落ちている）を**混ぜない** |
| **並列数** | 上げても速くならない（1 プロセスの推論資源を共有する）。`get_default_chunking_workers()` の実測コメント参照 |
| **1 ステップが長い** | SSE の keepalive（15 秒）と `started_at`（受付時刻）が前提。詳細は [`job_runtime.md` §2](./job_runtime.md) |

---

## 7. Embedding を Ollama にしない

`RegisterParams.provider` の既定は `"gemini"` で、**これが正しい**。
LLM 用途（Ollama）と Embedding 用途（Gemini）は別系統である。
変更するとベクトル次元が 3072 → 768 に変わり、**全コレクションの再作成と全件再登録**が要る。

---

## 8. ステップ番号は実行順と一致しない

| 系統 | 実行順（`*_STEP_IDS`）と番号のずれ |
|---|---|
| Support | `web`（⑤）の**後**に `no_info`（④'）が来る |
| Review | `web`（⑥）の**後**に `severity`（⑤）が来る |

文書やフロントの表示順は `STEP_IDS` / `REVIEW_STEP_IDS` の**定義順**に合わせる。

---

## 9. API スキーマを変えたら types.ts も変える

CI の frontend ゲートは `frontend/src/types.ts` の型エラー 1 個でマージを止める。
Python 側が全部緑でも通らない。対応表は [`api_contract.md` §6](./api_contract.md)。

---

## 10. 直してはいけないもの

| 見かけ上おかしいもの | 実際は |
|---|---|
| `RegisterParams.provider = "gemini"` | 意図的。Embedding だけは Gemini（§7） |
| Anthropic 経路が残っている | 後方互換（`provider="anthropic"` 明示時のみ）。姉妹リポジトリとの A/B 用 |
| `/api/qdrant/health` が Qdrant 停止中でも 200 | 意図的。画面でエラーを出し分けるため |
| `ConfirmResponse.status = "not_waiting"` が 200 | 意図的。タイムアウト済みの応答は異常ではない |
| `api/review.py` が `api/support.py` とほぼ同じ | 意図的な対称性 |
| `model: Optional[str] = None`（既定を入れていない） | 意図的（§5） |
| モデル名（`gemma4:12b-mlx` 等） | 手元に pull 済みのものだけを候補にしている。**勝手に「新しそうな名前」へ変えない** |

---

## 11. 変更履歴

| Version | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-09-16 | 新規作成。各モジュール文書に散っていた非自明な設計判断・過去の事故・ローカル LLM 固有の罠を 1 枚に集約した |
