# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s3_execute.py --vertical gov "住民票の写しの取り方は？"
#   uv run python grace/step_trace/s3_execute.py --vertical saas "Webhookの設定方法は？"
#   uv run python grace/step_trace/s3_execute.py --vertical ec "注文のキャンセル方法は？"
#   ※ 実 RAG 検索は Qdrant 起動＋各コレクション登録が必要
# ============================================================
# grace/step_trace/s3_execute.py
"""S3. ② Execute（内部 RAG → reasoning）。

`result = executor.execute(plan)` と、出典のラベル付け
`internal_citations = _collect_citations(result.step_results)` を取り出した
S3 トレース用スタブ。RAGSearchTool が allowed_collections で限定検索し、
スコア不足時は executor が web_search を動的挿入、ReasoningTool が
prompt_addendum を注入して回答を生成する様子を IN/Process/OUT で示す。

ANTHROPIC_API_KEY と Qdrant が必要。無ければ flow.md の gov 代表例で
OUT の構造だけを示す。

uv run python agent_support_example.py --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s3_execute.py --vertical gov "住民票の写しの取り方は？"
"""
from __future__ import annotations

import argparse
import sys

from _trace import banner, have_key, ipo, note_no_key

import agent_support_example as ase
from grace import create_executor, create_planner, create_tool_registry, get_config


def main() -> None:
    parser = argparse.ArgumentParser(description="S3: ② Execute トレース")
    parser.add_argument("query", nargs="?", default=ase.DEFAULT_QUERY)
    parser.add_argument("--vertical", choices=["gov", "saas", "ec"], default=None)
    args = parser.parse_args()

    banner("S3. ② Execute（executor + tools: 内部RAG）")

    config = get_config()
    profile = ase.PROFILES.get(args.vertical) if args.vertical else None
    config.qdrant.allowed_collections = list(profile.collections) if profile else []
    config.llm.prompt_addendum = profile.prompt_addendum if profile else ""

    if not have_key():
        note_no_key("executor.execute")
        ipo(
            in_=f'plan（②の計画）, allowed_collections={config.qdrant.allowed_collections}, '
                f'prompt_addendum={config.llm.prompt_addendum!r}',
            process=(
                "executor.execute(plan) … RAGSearchTool が Qdrant を allowed_collections で限定検索\n"
                "→（スコア不足なら web_search を動的挿入）→ ReasoningTool._build_prompt() が\n"
                "prompt_addendum を注入して回答生成。_collect_citations() で [社内]/[Web] ラベル付与"
            ),
            out=(
                "internal_answer = '住民票の写しは、お住まいの市区町村の窓口…（本文）'\n"
                "internal_citations = ['[社内] gov_faq_anthropic/住民票.md', ...]\n"
                "used_dynamic_web = False   # [Web] ラベルが無い＝内部だけで回答"
            ),
        )
        return

    # main() と同様、サービス未起動・鍵不正などをヒントつきで分かりやすく表示する
    try:
        tool_registry = create_tool_registry(config)
        planner = create_planner(config)
        executor = create_executor(config, tool_registry)

        plan = planner.create_plan(args.query)
        result = executor.execute(plan)
    except Exception as e:
        print(f"❌ 実行に失敗しました: {type(e).__name__}: {e}", file=sys.stderr)
        print(
            "  ヒント: Qdrant の起動（docker-compose -f docker-compose/docker-compose.yml up -d）"
            "と .env の API キーを確認してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    internal_answer = result.final_answer or ""
    internal_citations = ase._collect_citations(result.step_results)
    used_dynamic_web = any(c.startswith("[Web]") for c in internal_citations)

    steps_repr = "\n".join(
        f"    step{sr.step_id}: {sr.status} (sources={len(sr.sources)})"
        for sr in result.step_results
    )
    ipo(
        in_="plan（②の計画）, config.qdrant.allowed_collections, config.llm.prompt_addendum",
        process="executor.execute(plan) → RAG 限定検索 →（不足なら web_search 動的挿入）→ reasoning 生成",
        out=(
            f"result.overall_confidence={result.overall_confidence:.2f}\n"
            f"{steps_repr}\n"
            f"internal_answer[:60]={internal_answer[:60]!r}\n"
            f"internal_citations={internal_citations}\n"
            f"used_dynamic_web={used_dynamic_web}"
        ),
    )
    for sr in result.step_results:
        print(f"  step{sr.step_id}: {sr.status} (sources={len(sr.sources)})")
    if used_dynamic_web:
        print("  [web] executor が動的 Web 検索を使用（RAG スコア不足のため）")


if __name__ == "__main__":
    main()
