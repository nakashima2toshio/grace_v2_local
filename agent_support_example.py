# agent_support_example.py
"""GRACE-Support: 日本語ナレッジ駆動サポート・コパイロット（CLI）。

内部 RAG で回答し、**出典を必ず提示**する。根拠が不足すれば **Web フォールバック**
（v2）で裏取りし、内部×Web を**相互検証**する。問い合わせが「対応（アクション）」を
要する場合は、**擬似 ActionTool** を **HITL（CONFIRM 承認）** を通してから実行する
（v3。既定はドライラン＝実行せずログのみ）。なお根拠不足なら「わかりません」と誠実に
答えて有人対応へエスカレーションする。

**React マイグレーション後の構成**: 処理パイプライン本体は UI 非依存の
コアサービス `backend/app/core/support_agent.py`（イベント発行型）へ移設した。
本ファイルはコアを呼ぶ **CLI 用の薄いラッパ**（イベント → print のレンダラ）で、
判定ロジック（純関数群・プロファイル定義）は `backend/app/core/gates.py` /
`backend/app/core/verticals.py` から再エクスポートしている（tests / eval /
grace/step_trace の既存 import 互換のため）。Web UI は FastAPI
（`backend/app/main.py`）＋ React（`frontend/`）から同じコアを呼ぶ。

**業界特化（VerticalProfile）**: `--vertical {gov|saas|ec}` で業界プロファイルを適用し、
検索スコープ（allowed_collections）・エスカレ語・回答しきい値・アクション対応・
本人確認・方針（reasoning プロンプトへ注入）を切り替える。
設計は grace/doc/agent_support_verticals.md を参照。

設計書: grace/doc/agent_support_example.md ／ 業界特化: grace/doc/agent_support_verticals.md
上位計画: docs/migration_and_update.md

前提:
- `.env` に ANTHROPIC_API_KEY（LLM 用）と GOOGLE_API_KEY（Embedding 用）を設定
- Qdrant が起動済み（既定 http://localhost:6333）で RAG コレクションが登録済み

使い方::

    python agent_support_example.py "パスワードを忘れました"
    python agent_support_example.py --vertical gov "住民票の写しの取り方は？"
    python agent_support_example.py --vertical ec "返品したい"        # 本人確認→CONFIRM→ドライラン
    python agent_support_example.py --vertical saas -v "サービスが落ちています"  # 障害→escalate
    python agent_support_example.py --no-dry-run "解約したい"          # 擬似実行（実API連携は将来）
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, Optional

# --- 再エクスポート（後方互換）: tests / eval / grace/step_trace が参照する公開面 ---
from backend.app.core.gates import (  # noqa: F401
    NO_INFO_MARKERS,
    _answer_gate,
    _citation_text,
    _collect_citations,
    _collect_source_texts,
    _decide_action,
    _detect_no_info_answer,
    _match_keyword,
    _merge_citations,
    _pick_groundedness,
    _should_force_escalate,
    _should_rescue_unaffirmed,
    _web_citations,
    _web_source_texts,
    create_intent_classifier,
    create_no_info_judge,
)
from backend.app.core.support_agent import (  # noqa: F401
    SupportEvent,
    SupportResult,
    _perform_action,
    run_support_agent_core,
)
from backend.app.core.verticals import (  # noqa: F401
    DEFAULT_QUERY,
    INTENT_MODEL,
    PROFILES,
    ActionRequest,
    ActionType,
    Decision,
    Intent,
    VerticalProfile,
)
from grace import InterventionAction, InterventionResponse

# 非対話 CLI 用: CONFIRM/ESCALATE を自動承認するレスポンス（実行はドライランで安全）
_AUTO_PROCEED = InterventionResponse(action=InterventionAction.PROCEED)

# .env から ANTHROPIC_API_KEY / GOOGLE_API_KEY 等を読み込む（未導入でも続行）
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _banner(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def _cli_emit(event: SupportEvent) -> None:
    """コアの進捗イベントを従来どおりの CLI 出力（print）へ変換する。"""
    if event.type == "step":
        if event.status == "started" and event.title:
            _banner(event.title)
    elif event.type == "log":
        print(event.message)
    elif event.type == "error":
        print(event.message, file=sys.stderr)


def _render(result: SupportResult) -> None:
    """回答ゲートの判定に応じて応答を整形表示する。"""
    _banner("応答")
    if result.decision == "answer":
        print(result.answer or "（回答なし）")
        if result.warning:
            print("\n⚠️ 注意: この回答は出典による裏付けが十分ではありません。内容をご確認ください。")
        if result.used_web and result.contradiction:
            print("\n⚠️ 注意: 社内ナレッジと Web 情報で食い違いの可能性があります。")
        if result.citations:
            print("\n【出典】")
            for i, c in enumerate(result.citations, 1):
                print(f"  [{i}] {c}")
    else:  # escalate
        print("社内ナレッジにも Web 検索にも十分な根拠が見つかりませんでした。")
        print("→ 有人対応へエスカレーションします。")

    if result.action is not None:
        print(f"\n【アクション】種別={result.action.action_type} / 結果={result.action_result}")

    extra = ""
    if result.source_agreement is not None:
        extra = f" / 内部×Web 一致度={result.source_agreement:.2f}"
    vert = f" / vertical={result.vertical}" if result.vertical else ""
    intent = f" / intent={result.intent}" if result.intent else ""
    forced = " / 強制エスカレ" if result.forced_escalate else ""
    no_info = " / 情報なし回答検知" if result.no_info_detected else ""
    reused = " / Web再利用" if result.web_reused else ""
    print(f"\n[根拠] 支持率(groundedness)={result.groundedness:.2f} / "
          f"全体信頼度={result.overall_confidence:.2f} / decision={result.decision}"
          f" / web={'使用' if result.used_web else '不使用'}{extra}{vert}{intent}{forced}{no_info}{reused}")


def run_support_agent(
    query: str = DEFAULT_QUERY,
    verbose: bool = False,
    use_web: bool = True,
    do_action: bool = True,
    dry_run: bool = True,
    vertical: Optional[str] = None,
    identity: Optional[Dict[str, str]] = None,
) -> Optional[SupportResult]:
    """CLI 用エントリポイント。コアをイベント→print のレンダラ付きで実行する。

    HITL CONFIRM は非対話 CLI のため自動承認（`_AUTO_PROCEED`）で解決する
    （既定ドライランのため安全）。Web UI では画面上の承認（InterventionBridge）
    に差し替わる — 自動承認は CLI 限定であり Web 側へは持ち込まない。
    """
    result = run_support_agent_core(
        query,
        verbose=verbose,
        use_web=use_web,
        do_action=do_action,
        dry_run=dry_run,
        vertical=vertical,
        identity=identity,
        emit=_cli_emit,
        confirm=lambda _req: _AUTO_PROCEED,
    )
    if result is not None:
        # ⑦ 応答
        _render(result)
    return result


def main():
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
    args = parser.parse_args()

    identity: Optional[Dict[str, str]] = None
    if args.identity:
        identity = dict(
            pair.split("=", 1) for pair in args.identity if "=" in pair
        )

    try:
        run_support_agent(
            args.query, verbose=args.verbose, use_web=args.use_web,
            do_action=args.do_action, dry_run=args.dry_run, vertical=args.vertical,
            identity=identity,
        )
    except Exception as e:  # サービス未起動・鍵未設定などを分かりやすく表示
        print(f"❌ 実行に失敗しました: {type(e).__name__}: {e}", file=sys.stderr)
        print(
            "  ヒント: Qdrant の起動（docker-compose -f docker-compose/docker-compose.yml up -d）"
            "と .env の API キーを確認してください。",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
