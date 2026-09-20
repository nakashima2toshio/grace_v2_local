# grace/step_trace — ベンチマーク計測

GRACE パイプラインの KPI を計測するモジュールを置くパッケージ。

| ファイル | 役割 |
|---|---|
| `benchmark.py` | ベンチマーク計測（`BENCHMARK_QUERIES` / `BenchmarkRunner` / `BenchmarkLogger`）。CLI は無く、ライブラリとして import して使う |

```python
from grace.step_trace.benchmark import BENCHMARK_QUERIES, BenchmarkRunner

runner = BenchmarkRunner()              # モデル・プロバイダは config.llm から解決（既定は Ollama）
sessions = runner.run_query_set(fast=True)          # 代表 5 クエリ × 1 回
# sessions = runner.run_query_set(fast=True, mode="both")  # GRACE と ReAct を横並び
```

結果は `logs/benchmark_results.csv` に追記される（`agent_mode` 列で GRACE / ReAct を区別）。

> **⚠️ S0〜S9 のステップ別トレース（`s0_arg.py`〜`s9_render.py` と `_trace.py`）は
> 2026-09-20 に削除した。** CLI 本体（`agent_support_example.py`）を削除したことで
> 呼び出し先が無くなったため。実装は git 履歴に残る。
> ステップごとの挙動確認は `backend/tests/` のテスト、または
> `./run_dev.sh` のタイムライン表示（SSE）で行う。
