# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s6_web.py --vertical gov "住民票の写しの取り方は？"
#   uv run python grace/step_trace/s6_web.py --vertical saas --force-escalate "最新の障害情報は？"
#   uv run python grace/step_trace/s6_web.py --vertical ec --decision escalate "最新の配送遅延情報は？"
# ============================================================
# grace/step_trace/s6_web.py
"""S6. ⑤ Web フォールバック（内部が escalate かつ 非強制のときのみ）。

`if decision == "escalate" and use_web and not forced_escalate:` の分岐と、
その内側（web_search → reasoning → 相互検証、または再利用時は再検証のみ）を
取り出した S6 トレース用スタブ。gov 代表例は decision=="answer" のため
**丸ごとスキップ**される（この条件評価自体が S6 の主眼）。

escalate を強制するには --force-escalate を付ける。Web 検索の実呼び出しは
web バックエンド設定と鍵に依存するため、ここでは分岐条件の評価と、
使用する関数（_web_citations / _pick_groundedness / _merge_citations）の
役割を IN/Process/OUT で示す。

uv run python grace/step_trace/s6_web.py --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s6_web.py --force-escalate "来年の税制改正の予測は？"
"""
from __future__ import annotations

import argparse

from _trace import banner, have_key, ipo

import agent_support_example as ase
from grace import create_tool_registry, get_config


def main() -> None:
    parser = argparse.ArgumentParser(description="S6: ⑤ Web フォールバック トレース")
    parser.add_argument("query", nargs="?", default="住民票の写しの取り方は？")
    parser.add_argument("--vertical", choices=["gov", "saas", "ec"], default=None)
    parser.add_argument("--decision", choices=["answer", "escalate"], default="answer",
                        help="S5 までで確定した decision（既定 answer＝gov 代表例）")
    parser.add_argument("--force-escalate", action="store_true",
                        help="decision=escalate を強制し、⑤ ブロックに入る様子を見る")
    parser.add_argument("--no-web", dest="use_web", action="store_false")
    args = parser.parse_args()

    banner("S6. ⑤ Web フォールバック（tools.web_search → reasoning → 相互検証）")

    config = get_config()
    decision = "escalate" if args.force_escalate else args.decision
    use_web = args.use_web
    forced_escalate = False  # ⑤ は「強制エスカレでない」escalate のときだけ走る

    enter = decision == "escalate" and use_web and not forced_escalate
    ipo(
        in_=f'decision={decision!r}, use_web={use_web}, forced_escalate={forced_escalate}',
        process=(
            "`if decision == \"escalate\" and use_web and not forced_escalate:` を評価。\n"
            "True なら: executor が Web 使用済み→内部回答を本文スニペットで再検証のみ（web_reused=True）、\n"
            "         未使用→web_search → reasoning → 相互検証（SourceAgreementCalculator）\n"
            "         → _answer_gate 再判定 / _pick_groundedness / _merge_citations で SupportResult 再構築"
        ),
        out=(
            "⑤ ブロックへ進入" if enter
            else "分岐に入らない（support は S5 のまま）   # gov 代表例は decision='answer'"
        ),
    )

    if not enter:
        print("\n  ⑤ はスキップ（decision='answer' か --no-web か forced_escalate のため）")
        return

    print("\n  ⑤ に進入 → web_search を試行")
    if not have_key():
        print("  ⚠️ ANTHROPIC_API_KEY 未設定のため reasoning/相互検証はスキップ。web_search のみ試行します。")
    tool_registry = create_tool_registry(config)
    web_res = tool_registry.execute("web_search", query=args.query)
    web_output = web_res.output if (web_res and web_res.success) else None
    if web_output:
        web_citations = ase._web_citations(web_output)
        print(f"  [web] {len(web_citations)} 件の出典を取得")
        for c in web_citations:
            print(f"    - {c}")
    else:
        print("  [web] 有効な検索結果が得られませんでした（バックエンド設定/ネットワークに依存）")


if __name__ == "__main__":
    main()
