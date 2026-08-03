# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s0_arg.py --vertical gov "住民票の写しの取り方は？"
#   uv run python grace/step_trace/s0_arg.py --vertical saas "APIのレート制限は？" --no-web
#   uv run python grace/step_trace/s0_arg.py --vertical ec "返品したい" --identity order_id=1001
# ============================================================
# grace/step_trace/s0_arg.py
"""S0. 起動・引数解釈（main()→run_support_agent）。

`agent_support_example.py` の `main()` のうち「引数解釈」だけを取り出した
S0 トレース用スタブ。argparse がどんな args を作るか、`--identity KEY=VALUE`
（複数指定可）が `run_support_agent()` へ渡る前にどう dict 化されるかを
IN/Process/OUT で示す。LLM・Qdrant は一切呼ばない（鍵不要）。

uv run python agent_support_example.py --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s0_arg.py --vertical gov "住民票の写しの取り方は？"
"""
from __future__ import annotations

import argparse
import pprint
import sys
from typing import Dict, Optional

# _trace の import 時に quiet_logs()（実行基盤/httpx の INFO ログ抑制。
# GRACE_TRACE_VERBOSE=1 で表示）と load_dotenv()（.env 読み込み）が適用される。
from _trace import banner, ipo

DEFAULT_QUERY = "パスワードを忘れました"


def build_parser() -> argparse.ArgumentParser:
    """agent_support_example.main() と同一の引数体系のパーサを構築する。"""
    parser = argparse.ArgumentParser(
        description="GRACE-Support: 内部RAG＋出典／Web裏取り・相互検証／アクション＋HITL／業界特化(--vertical)"
    )
    parser.add_argument(
        "query", nargs="?", default=DEFAULT_QUERY,
        help="問い合わせ内容（省略時は既定の質問を使用）",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="支持率の内訳（supported/total/矛盾）など詳細を表示する",
    )
    parser.add_argument(
        "--vertical", choices=["gov", "saas", "ec"], default=None,
        help="業界プロファイルを適用（gov=自治体 / saas / ec）",
    )
    parser.add_argument(
        "--no-web", dest="use_web", action="store_false",
        help="Web フォールバックを無効化する（内部RAGのみ）",
    )
    parser.add_argument(
        "--no-action", dest="do_action", action="store_false",
        help="アクション（v3）を無効化する",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action=argparse.BooleanOptionalAction, default=True,
        help="アクションを実行せずログのみ（既定 ON。--no-dry-run で実連携/擬似実行）",
    )
    parser.add_argument(
        "--identity", action="append", default=None, metavar="KEY=VALUE",
        help="本人確認の識別子（例: --identity order_id=1001 --identity email=a@example.com。"
             "--no-dry-run 時に SUPPORT_IDENTITY_FILE の台帳と照合）",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # main() と同じ後処理: --identity KEY=VALUE（list）→ dict 化。
    # "=" を含まない指定は黙って捨てられる。この形で run_support_agent(identity=...) へ渡る。
    identity: Optional[Dict[str, str]] = None
    if args.identity:
        identity = dict(
            pair.split("=", 1) for pair in args.identity if "=" in pair
        )

    banner("S0. 起動・引数解釈（argparse → args / identity）")
    ipo(
        in_=f"argv={sys.argv[1:]!r}",
        process=(
            "build_parser() で main() と同一の引数体系を構築 → parser.parse_args()\n"
            "--identity KEY=VALUE（append）を dict へ変換（'=' を含まない指定は無視）\n"
            "この args / identity が run_support_agent(query, verbose, use_web, ...) の入力になる"
        ),
        out="vars(args) と identity（下記に pprint 表示）",
    )

    # pprint.pprint(object, stream=None, ...) の第 2 引数は「出力先ストリーム」。
    # ラベルは print で先に出し、対象は単独で pprint する（ラベルと同時に渡さない）。
    print()
    print("parser=:")
    pprint.pprint(parser)
    print("args=:")
    pprint.pprint(vars(args))   # Namespace は vars() で dict 化すると読みやすい
    print("identity=:")
    pprint.pprint(identity)


if __name__ == "__main__":
    main()
