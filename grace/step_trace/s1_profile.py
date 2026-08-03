# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s1_profile.py --vertical gov "住民票の写しの取り方は？"
#   uv run python grace/step_trace/s1_profile.py --vertical saas "APIのレート制限は？"
#   uv run python grace/step_trace/s1_profile.py --vertical ec "返品したい"
# ============================================================
# grace/step_trace/s1_profile.py
"""S1. 業界プロファイル適用（gov / saas / ec）。

`run_support_agent()` の冒頭で行う「プロファイル解決 → コア config への配線」だけを
取り出した S1 トレース用スタブ。`PROFILES[vertical]` を選び、検索スコープ
（`config.qdrant.allowed_collections`）と業界方針（`config.llm.prompt_addendum`）、
しきい値（notify/confirm）を config に書き込む様子を IN/Process/OUT で示す。

このステップは LLM を呼ばない（意図分類器・情報なし判定器は「用意するだけ」で、
候補一致時にのみ発火）。ANTHROPIC_API_KEY 無しでも動く。

uv run python agent_support_example.py --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s1_profile.py --vertical gov "住民票の写しの取り方は？"
"""
from __future__ import annotations

import argparse

from _trace import banner, ipo

import agent_support_example as ase
from grace import get_config


def main() -> None:
    parser = argparse.ArgumentParser(description="S1: 業界プロファイル適用トレース")
    parser.add_argument("query", nargs="?", default=ase.DEFAULT_QUERY)
    parser.add_argument("--vertical", choices=["gov", "saas", "ec"], default=None)
    args = parser.parse_args()

    banner(f"S1. 業界プロファイル適用（--vertical {args.vertical}）")

    # --- Process: config 取得 → プロファイル解決 → コア config へ配線 ---
    config = get_config()
    th = config.confidence.thresholds
    profile = ase.PROFILES.get(args.vertical) if args.vertical else None

    notify_th = profile.notify_th if (profile and profile.notify_th is not None) else th.notify
    confirm_th = profile.confirm_th if (profile and profile.confirm_th is not None) else th.confirm

    # tools は config 参照を保持するため、ここでの代入が実行時（S3）に効く
    config.qdrant.allowed_collections = list(profile.collections) if profile else []
    config.llm.prompt_addendum = profile.prompt_addendum if profile else ""

    ipo(
        in_=f'vertical={args.vertical!r}',
        process=(
            "get_config() で共通設定を取得（planner/executor/verifier/intervention もここで生成）\n"
            "PROFILES.get(vertical) で VerticalProfile を解決\n"
            "config.qdrant.allowed_collections / config.llm.prompt_addendum へ配線\n"
            "notify_th / confirm_th をプロファイル値（無ければ config 既定）で解決\n"
            "create_intent_classifier / create_no_info_judge は用意のみ（この時点では未発火）"
        ),
        out=(
            f"profile = {profile!r}\n"
            f"config.qdrant.allowed_collections = {config.qdrant.allowed_collections}\n"
            f"config.llm.prompt_addendum        = {config.llm.prompt_addendum!r}\n"
            f"notify_th={notify_th} / confirm_th={confirm_th}"
        ),
    )

    # --- 端末出力（run_support_agent と同じ体裁）---
    if profile is not None:
        banner(f"業界プロファイル: {profile.name}（--vertical {args.vertical}）")
        print(f"  検索スコープ: {', '.join(profile.collections) or '—'}"
              "（未登録コレクションは自動的に無視）")
        print(f"  しきい値: notify={notify_th} / confirm={confirm_th} / 本人確認={profile.require_identity}")
        if profile.prompt_addendum:
            print(f"  方針(reasoningへ注入): {profile.prompt_addendum}")
    else:
        print("  （--vertical 未指定：共通挙動。allowed_collections=[] / addendum='' / config 既定しきい値）")


if __name__ == "__main__":
    main()
