# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s4_confidence.py --vertical gov "住民票の写しの取り方は？"
#   uv run python grace/step_trace/s4_confidence.py --vertical saas "APIのレート制限は？"
#   uv run python grace/step_trace/s4_confidence.py --vertical ec "返品したい"
#   ※ ANTHROPIC_API_KEY があれば代表サンプルで実 verify を呼ぶ（Qdrant 不要）
# ============================================================
# grace/step_trace/s4_confidence.py
"""S4. ③ Confidence（支持率評価）。

`gres = verifier.verify(query, internal_answer, sources)` を取り出した S4
トレース用スタブ。GroundednessVerifier が回答を主張に分解し、各主張を
supported / contradicted / neutral に判定して支持率
（supported/(supported+contradicted)）を出す様子を IN/Process/OUT で示す。

ANTHROPIC_API_KEY があれば、代表的な回答＋出典本文で実際に verify を呼ぶ。
無ければ flow.md の gov 代表例（支持率=0.86）で OUT の構造だけを示す。

uv run python grace/step_trace/s4_confidence.py --vertical gov "住民票の写しの取り方は？"
"""
from __future__ import annotations

import argparse

from _trace import banner, have_key, ipo, note_no_key

from grace import get_config
from grace.confidence import create_groundedness_verifier

# 代表サンプル（内部 RAG が回答できた gov のケース）。実 Qdrant を使わず
# S4 単体を回すため、answer と sources（本文）をスタブが用意する。
SAMPLE_ANSWER = (
    "住民票の写しは、お住まいの市区町村の窓口（市民課等）またはコンビニ交付・"
    "郵送で請求できます。請求には本人確認書類が必要です。"
)
SAMPLE_SOURCES = [
    "Q: 住民票の写しはどこで取れますか？ A: 市区町村の窓口（市民課）、"
    "コンビニ交付、郵送で請求できます。窓口では本人確認書類が必要です。",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="S4: ③ Confidence トレース")
    parser.add_argument("query", nargs="?", default="住民票の写しの取り方は？")
    parser.add_argument("--vertical", choices=["gov", "saas", "ec"], default=None)
    args = parser.parse_args()

    banner("S4. ③ Confidence（GroundednessVerifier: 内部回答の裏付け）")

    config = get_config()

    if not have_key():
        note_no_key("verifier.verify")
        ipo(
            in_=f'query={args.query!r}, answer=SAMPLE_ANSWER, sources=SAMPLE_SOURCES（本文/識別子）',
            process=(
                "GroundednessVerifier.verify(query, answer, sources) … 回答を主張に分解し、\n"
                "各主張を supported/contradicted/neutral に判定。"
                "支持率=supported/(supported+contradicted)"
            ),
            out=(
                "gres = GroundednessResult(\n"
                "    support_rate=0.86, supported=3, contradicted=0, total=4,\n"
                "    has_contradiction=False, verified=True)"
            ),
        )
        return

    verifier = create_groundedness_verifier(config)
    gres = verifier.verify(args.query, SAMPLE_ANSWER, SAMPLE_SOURCES)
    ipo(
        in_=f'query={args.query!r}, answer=SAMPLE_ANSWER, sources={len(SAMPLE_SOURCES)} 件',
        process="GroundednessVerifier.verify(...) が主張分解→3値判定→支持率を集計",
        out=(
            f"gres = GroundednessResult(\n"
            f"    support_rate={gres.support_rate}, supported={gres.supported}, "
            f"contradicted={gres.contradicted}, total={gres.total},\n"
            f"    has_contradiction={gres.has_contradiction}, verified={gres.verified})"
        ),
    )
    print(f"\n  [groundedness] 支持率={gres.support_rate:.2f}"
          f"（判定可能 {gres.supported + gres.contradicted}/{gres.total} 主張）"
          f" / 出典数={len(SAMPLE_SOURCES)}")


if __name__ == "__main__":
    main()
