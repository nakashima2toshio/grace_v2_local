# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s9_render.py --vertical gov "住民票の写しの取り方は？"
#   uv run python grace/step_trace/s9_render.py --vertical saas "APIのレート制限は？"
#   uv run python grace/step_trace/s9_render.py --vertical ec "返品したい"
#   ※ LLM・Qdrant 不要。--vertical で代表例（SupportResult 最終形）を切り替えて
#     _render で整形表示する。query は表示用（整形処理は SupportResult のみに依存）。
# ============================================================
# grace/step_trace/s9_render.py
"""S9. ⑦ 応答整形（SupportResult → _render）。

`support.forced_escalate` / `support.intent` を確定し、`_render(support)` で
回答本文＋出典一覧＋根拠メタ行を整形表示する S9 トレース用スタブ。
各ステップ（S3〜S8）で少しずつ埋まった同一 SupportResult の最終形を、
業界別の代表例（--vertical gov / saas / ec）で組み立てて表示する（LLM・Qdrant 不要）。

- gov（既定）: flow.md §3 の代表例。内部 RAG のみで answer・出典 2 件
- saas: 内部 RAG のみで answer・config 既定しきい値（notify=0.7）帯の例
- ec: answer ＋ ⑥ Action（create_ticket, dry-run）・本人確認済みの例
  （【アクション】行と intent=request が根拠メタに乗る様子を見る）

整形処理（_render）自体は全業界共通で、SupportResult の値だけが変わる。

uv run python grace/step_trace/s9_render.py --vertical ec "返品したい"
"""
from __future__ import annotations

import argparse

from _trace import banner, ipo

import agent_support_example as ase

# 業界別の代表クエリ（query 省略時の表示用）
DEFAULT_QUERIES = {
    "gov": "住民票の写しの取り方は？",
    "saas": "APIのレート制限は？",
    "ec": "返品したい",
}


def build_sample(vertical: str = "gov") -> "ase.SupportResult":
    """S3〜S8 を経た SupportResult 最終形の業界別代表例。

    gov は flow.md §3「データの積み上がり（SupportResult 最終形）」に一致する。
    """
    if vertical == "saas":
        return ase.SupportResult(
            answer=(
                "API のレート制限は Free プランで 60 リクエスト/分、Pro プランで "
                "600 リクエスト/分です。超過時は 429 が返るため、Retry-After ヘッダに"
                "従って再試行してください。詳細は公式ドキュメントをご確認ください。"
            ),
            citations=[
                "[社内] saas_api_anthropic/rate_limit.md",
                "[社内] saas_docs_anthropic/plans.md",
            ],
            groundedness=0.75,
            groundedness_decided=3,
            decision="answer",
            warning=False,
            used_web=False,
            vertical="saas",
            overall_confidence=0.72,
        )
    if vertical == "ec":
        return ase.SupportResult(
            answer=(
                "返品は商品到着後 30 日以内に承ります。未開封・未使用が条件です。"
                "マイページの注文履歴から返品を申請してください。"
            ),
            citations=[
                "[社内] ec_policy_anthropic/返品規定.md",
                "[社内] ec_faq_anthropic/返品手続き.md",
            ],
            groundedness=0.82,
            groundedness_decided=4,
            decision="answer",
            warning=False,
            used_web=False,
            vertical="ec",
            overall_confidence=0.76,
            # ⑥ Action: 「返品」→ create_ticket（dry-run・本人確認済み）。
            # args / action_result は _decide_action と dry-run バックエンドの実出力形式に合わせる
            action=ase.ActionRequest(
                action_type="create_ticket",
                args={"query": "返品したい", "matched": "返品"},
                requires_confirmation=True,
            ),
            action_result="[DRY-RUN] 'create_ticket' を実行"
                          "（ログのみ・args={'query': '返品したい', 'matched': '返品'}）",
            identity_checked=True,
        )
    # gov（既定）: flow.md §3 の代表例
    return ase.SupportResult(
        answer=(
            "住民票の写しは、お住まいの市区町村の窓口（市民課等）またはコンビニ交付・"
            "郵送で請求できます。本人確認書類が必要です。詳しくは担当課の案内ページを"
            "ご確認ください。"
        ),
        citations=[
            "[社内] gov_faq_anthropic/住民票.md",
            "[社内] gov_faq_anthropic/窓口案内.md",
        ],
        groundedness=0.86,
        groundedness_decided=3,
        decision="answer",
        warning=False,
        used_web=False,
        vertical="gov",
        overall_confidence=0.78,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="S9: ⑦ 応答整形 トレース")
    parser.add_argument("query", nargs="?", default=None,
                        help="問い合わせ（表示用。整形処理は SupportResult のみに依存）")
    parser.add_argument("--vertical", choices=["gov", "saas", "ec"], default="gov",
                        help="表示する代表例（SupportResult 最終形）を切り替える")
    args = parser.parse_args()

    query = args.query if args.query is not None else DEFAULT_QUERIES[args.vertical]

    banner("S9. ⑦ 応答整形（_render → SupportResult 返却）")
    print(f"❓ 問い合わせ: {query}（--vertical {args.vertical} の代表例）")

    support = build_sample(args.vertical)
    # run_support_agent の末尾と同じ確定処理（KPI メタ）。
    # gov/saas の FAQ 質問は意図分類器が未発火のため None、
    # ec は action_map「返品」→ 二段判定で意図分類が走り intent="request" となる代表例。
    support.forced_escalate = False
    support.intent = "request" if args.vertical == "ec" else None

    ipo(
        in_="support（S3〜S8 で確定した SupportResult）",
        process=(
            "support.forced_escalate / support.intent を確定した後、\n"
            "_render(support) が回答本文＋出典一覧＋根拠メタ行を整形表示し、\n"
            "run_support_agent() が support を return"
        ),
        out=(
            f"decision={support.decision!r}, groundedness={support.groundedness}, "
            f"vertical={support.vertical!r}, intent={support.intent!r}\n"
            "端末表示（下記）＋ 呼び出し元へ SupportResult を返却"
        ),
    )

    # 実際の整形表示（agent_support_example._render をそのまま使用）
    ase._render(support)


if __name__ == "__main__":
    main()
