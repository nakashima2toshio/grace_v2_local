# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s7_no_info.py --vertical gov "住民票の写しの取り方は？"   # answered（候補句なし）
#   uv run python grace/step_trace/s7_no_info.py --vertical saas "APIのレート制限は？"       # answered（候補句なし）
#   uv run python grace/step_trace/s7_no_info.py --vertical ec                               # 候補句あり→第2段判定
#   uv run python grace/step_trace/s7_no_info.py --web-only "この商品の入荷予定日は？"        # Web出典のみ→必須判定
#   uv run python grace/step_trace/s7_no_info.py --answer "該当する情報が見当たりません" "在庫は？"
# ============================================================
# grace/step_trace/s7_no_info.py
"""S7. ④' 情報なし回答検知。

`_detect_no_info_answer(query, answer, judge, force_judge=web_only)` を取り出した
S7 トレース用スタブ。第 1 段は定型句（NO_INFO_MARKERS）候補検出、第 2 段は
軽量 LLM（answered/no_info）。出典が Web のみ（社内根拠ゼロ）の回答は
force_judge=True で候補句がなくても必須判定する。

`--vertical` は検知ロジック自体には影響しない（プロファイル非依存の純関数＋判定器）が、
業界別の代表サンプル（query/answer の既定値）を切り替える:

- gov / saas: 実質回答・候補句なし → 第 1 段不一致 → LLM 未実行 → no_info=False（answer 維持）
- ec: 「見当たりません」を含む案内のみの回答 → 第 1 段一致 → 第 2 段（LLM）で最終判定
  （鍵が無ければ judge=None → 従来どおり回答を通す）

query / --answer を明示した場合はサンプルより優先される。

uv run python grace/step_trace/s7_no_info.py --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s7_no_info.py --web-only "この商品の入荷予定日は？"
"""
from __future__ import annotations

import argparse

from _trace import banner, have_key, ipo

import agent_support_example as ase
from grace import get_config

# 業界別の代表サンプル（query, answer）。ec のみ「情報なし回答」の候補句を含む。
SAMPLES = {
    "gov": (
        "住民票の写しの取り方は？",
        "住民票の写しは、市区町村の窓口（市民課等）・コンビニ交付・郵送で請求できます。"
        "本人確認書類が必要です。",
    ),
    "saas": (
        "APIのレート制限は？",
        "API のレート制限は Free プランで 60 リクエスト/分、Pro プランで 600 リクエスト/分です。"
        "超過時は 429 が返り、Retry-After ヘッダに従って再試行してください。",
    ),
    "ec": (
        "この商品の入荷予定日は？",
        "該当する入荷予定日の情報が見当たりませんでした。商品ページの「再入荷通知」または"
        "カスタマーサポートへのお問い合わせをご利用ください。",
    ),
}
DEFAULT_VERTICAL = "gov"


def main() -> None:
    parser = argparse.ArgumentParser(description="S7: ④' 情報なし回答検知 トレース")
    parser.add_argument("query", nargs="?", default=None,
                        help="質問（省略時は --vertical の代表サンプル）")
    parser.add_argument("--vertical", choices=["gov", "saas", "ec"], default=None,
                        help="業界別の代表サンプル（query/answer の既定値）を選ぶ（検知ロジック自体は共通）")
    parser.add_argument("--answer", default=None,
                        help="検証する回答本文（省略時は --vertical の代表サンプル）")
    parser.add_argument("--web-only", action="store_true",
                        help="出典が Web のみ（force_judge=True）として必須判定させる")
    args = parser.parse_args()

    sample_query, sample_answer = SAMPLES[args.vertical or DEFAULT_VERTICAL]
    query = args.query if args.query is not None else sample_query
    answer = args.answer if args.answer is not None else sample_answer

    banner("S7. ④' 情報なし回答検知（_detect_no_info_answer）")

    config = get_config()
    no_info_judge = ase.create_no_info_judge(config) if have_key() else None

    # 第 1 段（候補検出）は LLM 不要なので、先に marker だけ確認して見せる
    marker = ase._match_keyword(answer, ase.NO_INFO_MARKERS)
    web_only = args.web_only

    no_info, matched = ase._detect_no_info_answer(
        query, answer, no_info_judge, force_judge=web_only
    )

    ipo(
        in_=(f'query={query!r}, answer[:40]={answer[:40]!r},\n'
             f'force_judge(web_only)={web_only}, judge={"あり" if no_info_judge else "None"}'),
        process=(
            "第1段: _match_keyword(answer, NO_INFO_MARKERS) で候補句を検出\n"
            "  → 候補なし かつ not force_judge なら (False, None)（LLM 未実行）\n"
            "  → 候補あり でも judge=None（鍵なし）なら (False, marker)（従来どおり回答を通す）\n"
            "第2段: judge(query, answer) で answered/no_info を判定\n"
            "  → answered なら (False, marker)、no_info/判定失敗なら (True, marker)（安全側 escalate）"
        ),
        out=(
            f"第1段の候補句 marker={marker!r}\n"
            f"(no_info, matched_marker) = ({no_info}, {matched!r})"
        ),
    )

    if no_info:
        trigger = f"候補句 '{matched}'" if matched is not None else "出典が Web のみ"
        print(f"\n  [gate] 情報なし回答を検知（{trigger}）→ 有人対応へエスカレーション")
    elif matched is not None and no_info_judge is None:
        print(f"\n  [gate] 候補句 '{matched}' を検知したが判定器なし（鍵未設定）→ decision='answer' を維持")
    else:
        print("\n  [gate] 実質回答（answered）→ decision='answer' を維持")


if __name__ == "__main__":
    main()
