# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s5_gate.py --vertical gov "固定資産税の減免を個別に判断してほしい"
#   uv run python grace/step_trace/s5_gate.py --vertical saas "本番環境で障害が発生している"
#   uv run python grace/step_trace/s5_gate.py --vertical ec "決済の返金がされない"
#   uv run python grace/step_trace/s5_gate.py --vertical gov "住民票の取り方は？" --support-rate 0.6
# ============================================================
# grace/step_trace/s5_gate.py
"""S5. ④ 回答ゲート＋強制エスカレ（二段判定）。

`_answer_gate()`（支持率・出典数 → answer/escalate）と
`_should_force_escalate()`（エスカレ語×意図分類の二段判定）、
`_should_rescue_unaffirmed()`（出典付き・矛盾なし回答の救済）を取り出した
S5 トレース用スタブ。いずれも純関数中心で、gov 代表例では意図分類 LLM は
呼ばれない（第 1 段でエスカレ語に不一致 → 第 2 段スキップ・追加コスト 0）。

意図分類が要る分岐（例: gov「減免を個別に判断してほしい」）は
ANTHROPIC_API_KEY があれば実分類、無ければ classify=None（安全側）で示す。

uv run python grace/step_trace/s5_gate.py --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s5_gate.py --vertical gov "固定資産税の減免を個別に判断してほしい"
"""
from __future__ import annotations

import argparse

from _trace import banner, have_key, ipo

import agent_support_example as ase
from grace import get_config


def main() -> None:
    parser = argparse.ArgumentParser(description="S5: ④ 回答ゲート＋強制エスカレ トレース")
    parser.add_argument("query", nargs="?", default="住民票の写しの取り方は？")
    parser.add_argument("--vertical", choices=["gov", "saas", "ec"], default=None)
    parser.add_argument("--support-rate", type=float, default=0.86,
                        help="③ Confidence の支持率（既定 0.86＝gov 代表例）")
    args = parser.parse_args()

    banner("S5. ④ 回答ゲート＋強制エスカレ（二段判定）")

    config = get_config()
    th = config.confidence.thresholds
    profile = ase.PROFILES.get(args.vertical) if args.vertical else None
    notify_th = profile.notify_th if (profile and profile.notify_th is not None) else th.notify
    confirm_th = profile.confirm_th if (profile and profile.confirm_th is not None) else th.confirm

    # 意図分類器（第 2 段）: エスカレ語に一致したときだけ発火する。
    classify = ase.create_intent_classifier(config) if have_key() else None

    # 代表例に合わせた入力（内部 RAG が answer 可能・出典 3 件）
    support_rate = args.support_rate
    verified, citation_count = True, 3

    # --- 第 1 段: 回答ゲート ---
    decision, warning = ase._answer_gate(
        support_rate, verified, citation_count, notify_th, confirm_th
    )

    # --- 第 2 段: 強制エスカレ（エスカレ語 × 意図分類）---
    forced, matched_kw, intent = ase._should_force_escalate(args.query, profile, classify)
    if forced:
        decision, warning = "escalate", False

    ipo(
        in_=(f'support_rate={support_rate}, verified={verified}, citation_count={citation_count}, '
             f'notify_th={notify_th}, confirm_th={confirm_th}\n'
             f'query={args.query!r}, profile={args.vertical}'),
        process=(
            "_answer_gate(...) が 支持率≥notify かつ 出典≥1 → answer を判定\n"
            "_should_force_escalate(query, profile, classify): 第1段 _match_keyword で候補検出、\n"
            "  一致時のみ classify（意図分類）。question は誤検知抑止、request/incident は強制エスカレ\n"
            "_should_rescue_unaffirmed は decision!='escalate' のため今回は不発（救済不要）"
        ),
        out=(
            f"(decision, warning) = ({decision!r}, {warning})\n"
            f"forced_escalate={forced}, matched_kw={matched_kw!r}, intent={intent!r}"
        ),
    )

    if forced:
        print(f"\n  [profile] エスカレ語 '{matched_kw}'（意図={intent or '不明'}）を検知 → 有人対応へ")
    elif matched_kw is not None:
        print(f"\n  [profile] エスカレ語候補 '{matched_kw}' は FAQ 質問（意図=question）→ 誤検知抑止・通常フロー継続")
    else:
        print("\n  [gate] エスカレ語なし → 意図分類 LLM は未実行（追加コスト 0）")


if __name__ == "__main__":
    main()
