# grace/step_trace — S0〜S9 ステップ別トレース

`agent_support_example.py`（GRACE-Support）の `run_support_agent()` を、
[`../doc/agent_support_example_flow.md`](../docs/agent_support_example_flow.md) の
**S0〜S9** に沿って 1 ステップずつ切り出した実行トレース用スタブ集。
各ファイルはそのステップの実コードをそのまま呼び、**IN → Process → OUT** の
3 段（フロー図 §2 の読み方）で標準出力に示す。

| ファイル | ステップ | 内容 | 実行要件 |
|---|---|---|---|
| `s0_arg.py` | S0 | 起動・引数解釈（argparse → args） | なし |
| `s1_profile.py` | S1 | 業界プロファイル適用（`PROFILES`→config 配線） | なし |
| `s2_plan.py` | S2 | ① Plan（`planner.create_plan`） | ANTHROPIC_API_KEY |
| `s3_execute.py` | S3 | ② Execute（内部RAG→reasoning） | ANTHROPIC_API_KEY・Qdrant |
| `s4_confidence.py` | S4 | ③ Confidence（`GroundednessVerifier.verify`） | ANTHROPIC_API_KEY |
| `s5_gate.py` | S5 | ④ 回答ゲート＋強制エスカレ（二段判定） | なし（分岐で任意 LLM） |
| `s6_web.py` | S6 | ⑤ Web フォールバック（条件評価） | 任意（web/LLM） |
| `s7_no_info.py` | S7 | ④' 情報なし回答検知 | 任意（LLM） |
| `s8_action.py` | S8 | ⑥ Action（本人確認→CONFIRM→dry-run） | なし（dry-run） |
| `s9_render.py` | S9 | ⑦ 応答整形（`_render`→SupportResult） | なし |

**設計方針**: 環境（`ANTHROPIC_API_KEY` / Qdrant）があれば本物のデータで、
無ければ各スタブの代表サンプル（flow.md の gov 例）で構造だけを示す。
共通処理（import パス設定・IN/Process/OUT 表示）は `_trace.py` に集約。

## 実行例

```bash
uv run python grace/step_trace/s0_arg.py     --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s1_profile.py --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s5_gate.py    --vertical gov "固定資産税の減免を個別に判断してほしい"
uv run python grace/step_trace/s8_action.py  --vertical ec  "返品したい"
uv run python grace/step_trace/s9_render.py
```

> 参照: 設計書 [`../doc/agent_support_example.md`](../docs/agent_support_example.md) ／
> 実行トレース [`../doc/agent_support_example_flow.md`](../docs/agent_support_example_flow.md)
