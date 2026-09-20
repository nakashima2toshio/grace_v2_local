# step_trace ドキュメント索引

**Version 2.0** | 最終更新: 2026-09-20

`grace/step_trace/` のモジュールドキュメント一覧。

## モジュール

| モジュール | ドキュメント | 役割 | 備考 |
|---|---|---|---|
| `benchmark.py` | （未作成） | GRACE のベンチマーク計測（`BENCHMARK_QUERIES` / `BenchmarkRunner`） | **CLI は無い。** ライブラリとして `from grace.step_trace.benchmark import ...` で呼ぶ。使い方は [`../README.md`](../README.md) |

## 削除済み: S0〜S9 ステップ別トレース

`s0_arg.py`〜`s9_render.py`（10 モジュール）と共有ヘルパ `_trace.py`、および
それぞれの doc は **2026-09-20 に削除した**。これらは CLI
（`agent_support_example.py`）の `run_support_agent()` を 1 ステップずつ切り出した
トレース用スタブ集であり、CLI 本体の削除にともない呼び出し先を失ったため。
実装・文書とも git 履歴に残る。

代替手段:

| 目的 | 代替 |
|---|---|
| ステップ単位の挙動確認 | `backend/tests/`（`test_support_agent_core.py` のステップイベント検証ほか） |
| 1 リクエストの流れを目で追う | `./run_dev.sh` → :5173 のタイムライン表示（SSE でステップ進捗を配信） |
| パイプラインの設計把握 | [`backend/docs/support_flow.md`](../../../backend/docs/support_flow.md) |

## 関連ドキュメント

- 設計書: [`backend/docs/support_flow.md`](../../../backend/docs/support_flow.md)
- 業界特化の全体設計: [`backend/docs/verticals_and_rulesets.md`](../../../backend/docs/verticals_and_rulesets.md)

## 変更履歴

| バージョン | 変更内容 |
|-----------|---------|
| 2.0 | S0〜S9 のトレース用スタブと doc を削除（CLI `agent_support_example.py` の削除にともない呼び出し先を喪失）。索引を `benchmark.py` のみへ縮約（2026-09-20） |
| 1.1 | 全体フロー図（S0〜S9 の位置づけ）とステップ別の実行要件列を追加。S0/S7/S9 の改修（共通 CLI 書式への統一・S0 の IN/Process/OUT 表示と identity dict 化・S9 の業界別代表例）と S3 の失敗時ヒント表示を反映（2026-07-10） |
| 1.0 | 初版。S0〜S9 の 10 モジュール doc と本索引を作成（2026-07-09） |
