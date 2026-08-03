# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s2_plan.py --vertical gov "住民票の写しの取り方は？"
#   uv run python grace/step_trace/s2_plan.py --vertical saas "SSO設定の手順を教えて"
#   uv run python grace/step_trace/s2_plan.py --vertical ec "注文のキャンセル方法は？"
# ============================================================
# grace/step_trace/s2_plan.py
"""S2. ① Plan（質問分類・計画）。

`plan = planner.create_plan(query)` だけを取り出した S2 トレース用スタブ。
LLM がクエリの複雑度を推定し、rag_search（必要なら reasoning）ステップからなる
ExecutionPlan を生成する様子を IN/Process/OUT で示す。

ANTHROPIC_API_KEY があれば実際に planner を呼ぶ。無ければ flow.md の gov 代表例で
OUT の構造だけを示す。

uv run python agent_support_example.py --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s2_plan.py --vertical gov "住民票の写しの取り方は？"
"""
from __future__ import annotations

import argparse

from _trace import banner, have_key, ipo, note_no_key

import agent_support_example as ase
from grace import create_planner, get_config


def main() -> None:
    parser = argparse.ArgumentParser(description="S2: ① Plan トレース")
    parser.add_argument("query", nargs="?", default=ase.DEFAULT_QUERY)
    parser.add_argument("--vertical", choices=["gov", "saas", "ec"], default=None)
    args = parser.parse_args()

    banner("S2. ① Plan（planner.create_plan）")
    print(f"❓ 問い合わせ: {args.query}")

    config = get_config()
    profile = ase.PROFILES.get(args.vertical) if args.vertical else None
    # S1 相当の配線（プランナの利用可能コレクションにも効く）
    config.qdrant.allowed_collections = list(profile.collections) if profile else []

    if not have_key():
        note_no_key("planner.create_plan")
        ipo(
            in_=f'query={args.query!r}',
            process="Planner.create_plan(query) … LLM が複雑度を推定し ExecutionPlan を生成",
            out=(
                "plan = ExecutionPlan(\n"
                "    steps=[PlanStep(step_id=1, action='rag_search', ...),\n"
                "           PlanStep(step_id=2, action='reasoning', ...)],\n"
                "    complexity=<0.0-1.0>)   # 例: 2 ステップ / complexity=0.35"
            ),
        )
        return

    planner = create_planner(config)
    plan = planner.create_plan(args.query)

    steps_repr = "\n".join(
        f"    PlanStep(step_id={s.step_id}, action={s.action!r}, "
        f"collection={s.collection!r}, depends_on={s.depends_on})"
        for s in plan.steps
    )
    ipo(
        in_=f'query={args.query!r}, allowed_collections={config.qdrant.allowed_collections}',
        process="Planner.create_plan(query) … LLM が複雑度を推定し rag_search/reasoning 計画を生成",
        out=(
            f"plan = ExecutionPlan(\n"
            f"  original_query={plan.original_query!r},\n"
            f"  complexity={plan.complexity:.2f}, estimated_steps={plan.estimated_steps},\n"
            f"  steps=[\n{steps_repr}\n  ])"
        ),
    )
    print(f"\n  [plan] {len(plan.steps)} ステップ (complexity={plan.complexity:.2f})")


if __name__ == "__main__":
    main()
