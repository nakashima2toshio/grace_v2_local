# ============================================================
# 実行例（uv run）:
#   uv run python grace/step_trace/s8_action.py --vertical gov "保育園の申請様式がほしい"
#   uv run python grace/step_trace/s8_action.py --vertical saas "ログインエラーの不具合を調査してほしい"
#   uv run python grace/step_trace/s8_action.py --vertical ec "返品したい"
# ============================================================
# grace/step_trace/s8_action.py
"""S8. ⑥ Action（本人確認 → HITL CONFIRM → ActionTool 実行）。

`action = _decide_action(query, decision, profile, classify)` と、必要時の
`_perform_action(action, handler, backend, identity_verifier, identity)` を
取り出した S8 トレース用スタブ。既定ドライラン（副作用なし）で
本人確認 → CONFIRM 承認 → backend.execute の順を IN/Process/OUT で示す。

gov 代表例（「住民票の写しの取り方」）は action_map に候補なし → action=None
→ ⑥ に入らない。EC「返品したい」は create_ticket → 本人確認（require_identity）
→ CONFIRM → dry-run 実行。

uv run python grace/step_trace/s8_action.py --vertical gov "住民票の写しの取り方は？"
uv run python grace/step_trace/s8_action.py --vertical ec "返品したい"
"""
from __future__ import annotations

import argparse

from _trace import banner, have_key, ipo

import agent_support_example as ase
from grace import create_intervention_handler, get_config
from support_actions import create_action_backend, create_identity_verifier


def main() -> None:
    parser = argparse.ArgumentParser(description="S8: ⑥ Action トレース")
    parser.add_argument("query", nargs="?", default="住民票の写しの取り方は？")
    parser.add_argument("--vertical", choices=["gov", "saas", "ec"], default=None)
    parser.add_argument("--decision", choices=["answer", "escalate"], default="answer")
    args = parser.parse_args()

    banner("S8. ⑥ Action（本人確認 → intervention CONFIRM → ActionTool[dry-run]）")

    config = get_config()
    profile = ase.PROFILES.get(args.vertical) if args.vertical else None
    classify = ase.create_intent_classifier(config) if have_key() else None

    # --- 第 1 段: アクション決定（二段判定）---
    action = ase._decide_action(args.query, args.decision, profile, classify)

    ipo(
        in_=f'query={args.query!r}, decision={args.decision!r}, profile={args.vertical}',
        process=(
            "_decide_action(): escalate→escalate_to_human。answer なら第1段 _match_keyword\n"
            "  (profile.action_map / 既定マッピング)、候補あり かつ 意図=question は起票せず None\n"
            "action があれば _perform_action(): 本人確認 → CONFIRM 承認 → backend.execute（dry-run）"
        ),
        out=f"action = {action!r}",
    )

    if action is None:
        print("\n  ⑥ に入らない（action_map に候補なし → 起票せず回答のみ）")
        return

    print(f"\n  [action] 種別={action.action_type}（要承認={action.requires_confirmation}）")
    handler = create_intervention_handler(
        config,
        on_notify=lambda msg: print(f"   [intervention/notify] {msg}"),
        on_confirm=lambda _req: ase._AUTO_PROCEED,
        on_escalate=lambda _req: ase._AUTO_PROCEED,
    )
    backend = create_action_backend(dry_run=True)  # 既定ドライラン（副作用なし）
    require_identity = bool(profile and profile.require_identity)
    identity_verifier = create_identity_verifier(dry_run=True) if require_identity else None

    result_msg = ase._perform_action(
        action, handler, backend, identity_verifier=identity_verifier, identity=None
    )
    print(f"  [action] {result_msg}")
    print(f"  [action] identity_checked={require_identity} / backend={backend.name}")


if __name__ == "__main__":
    main()
