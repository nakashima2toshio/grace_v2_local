# backend/app/core/support_agent.py
"""GRACE-Support コアサービス（UI 非依存・イベント発行型）。

`agent_support_example.py` の `run_support_agent()` から標準出力（print/_banner）
への密結合を分離した版。処理パイプライン（①〜⑥、④'・④救済・二段判定）は
CLI 版と同一で、変えたのは「入出力の経路」だけ:

- 途中経過は `emit(SupportEvent)` コールバックで通知する
  （CLI はこれを print に、Web は SSE ストリームに配線する）
- ⑥ の HITL CONFIRM は `confirm` コールバックで解決する
  （CLI は自動承認 `AUTO_PROCEED`、Web は `InterventionBridge` の承認待ち。
  Web 側に自動承認を持ち込まないこと＝受け入れ条件 §5-2）

設計書: grace/doc/agent_support_example.md ／ 業界特化: grace/doc/agent_support_verticals.md
"""
from __future__ import annotations

import copy
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from backend.app.core.gates import (
    _answer_gate,
    _citation_text,
    _collect_citations,
    _collect_source_texts,
    _decide_action,
    _detect_no_info_answer,
    _merge_citations,
    _pick_groundedness,
    _should_force_escalate,
    _should_rescue_unaffirmed,
    _web_citations,
    _web_source_texts,
    create_intent_classifier,
    create_no_info_judge,
)
from backend.app.core.verticals import (
    DEFAULT_QUERY,
    INTENT_MODEL,
    PROFILES,
    ActionRequest,
    Decision,
    Intent,
)
from grace import (
    ActionDecision,
    InterventionAction,
    InterventionLevel,
    InterventionRequest,
    InterventionResponse,
    create_executor,
    create_intervention_handler,
    create_planner,
    create_source_agreement_calculator,
    create_tool_registry,
    get_config,
)
from grace.confidence import create_groundedness_verifier
from support_actions import create_action_backend, create_identity_verifier

# 非対話 CLI 用: CONFIRM/ESCALATE を自動承認するレスポンス（実行はドライランで安全）。
# Web（backend.app.api）では使用禁止 — 承認は必ず InterventionBridge を経由する。
AUTO_PROCEED = InterventionResponse(action=InterventionAction.PROCEED)


# =============================================================================
# イベントモデル
# =============================================================================

# パイプラインのステップ ID（UI のタイムライン表示と対応）
STEP_IDS = (
    "profile",     # S1 業界プロファイル適用（--vertical 指定時のみ）
    "plan",        # ① Plan
    "execute",     # ② Execute（内部RAG → reasoning）
    "confidence",  # ③ Groundedness
    "gate",        # ④ 回答ゲート＋強制エスカレ＋④-救済
    "web",         # ⑤ Web フォールバック
    "no_info",     # ④' 情報なし回答検知
    "action",      # ⑥ Action（本人確認 → HITL CONFIRM → 実行）
)


@dataclass
class SupportEvent:
    """パイプラインの進捗イベント。

    type:
      - "step"         : ステップの開始/終了/スキップ（status = started/finished/skipped）
      - "log"          : 途中経過メッセージ（CLI の print に相当）
      - "intervention" : HITL 承認待ち（フロントは CONFIRM モーダルを表示）
      - "result"       : 最終結果（data に SupportResult の dict）
      - "error"        : 実行エラー
    """

    type: str
    step: Optional[str] = None
    status: Optional[str] = None
    title: str = ""
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


EmitFn = Callable[[SupportEvent], None]
ConfirmFn = Callable[[InterventionRequest], InterventionResponse]


@dataclass
class SupportResult:
    """サポート回答の結果。"""

    answer: Optional[str]
    citations: List[str] = field(default_factory=list)
    groundedness: float = 0.0
    groundedness_decided: int = 0      # 判定できた主張数（supported+contradicted）。0=判定不能（中立）
    decision: Decision = "escalate"
    warning: bool = False              # 中信頼（未確認）の注意書きを付けるか
    used_web: bool = False             # Web を使ったか（executor の動的 Web 検索 or ⑤ フォールバック）
    source_agreement: Optional[float] = None  # 内部×Web の意味的一致度（相互検証）
    contradiction: bool = False        # 矛盾の可能性
    action: Optional[ActionRequest] = None    # 実施（予定）のアクション
    action_result: Optional[str] = None       # アクションの結果メッセージ
    vertical: Optional[str] = None            # 適用した業界プロファイル
    overall_confidence: float = 0.0
    intent: Optional[Intent] = None           # 意図分類の結果（二段判定が走った場合）
    forced_escalate: bool = False             # エスカレ語による強制エスカレか（KPI 計測用）
    identity_checked: bool = False            # 本人確認ステップが起動したか（KPI 計測用）
    no_info_detected: bool = False            # 「情報なし回答」検知で escalate に倒したか
    web_reused: bool = False                  # ⑤ で executor の Web 結果を再利用したか（重複推論の省略）


def result_to_dict(result: SupportResult) -> Dict[str, Any]:
    """SupportResult を JSON 化可能な dict にする（API レスポンス・result イベント用）。"""
    return asdict(result)


# =============================================================================
# アクション実行（本人確認 → HITL CONFIRM → バックエンド）
# =============================================================================

def _perform_action(
    action: ActionRequest,
    handler,
    backend,
    identity_verifier=None,
    identity: Optional[Dict[str, str]] = None,
    emit_log: Optional[Callable[[str], None]] = None,
) -> str:
    """本人確認 → HITL（CONFIRM 承認）→ バックエンド実行 の順でアクションを行う。

    - 本人確認（identity_verifier 指定時）: 提示された識別子を照合し、未確認なら
      アクションを実行せず有人対応へ引き継ぐ（安全側）
    - CONFIRM: 副作用のある操作（requires_confirmation=True。create_ticket /
      send_reply 等）は必ず intervention の承認を経由する。承認待ちがタイムアウト
      した場合（timeout_reached）は実行せず有人対応へエスカレーションする
      （安全側＝escalate に倒す）。escalate_to_human は引き継ぎそのもの
      （requires_confirmation=False）なので承認を経由せず直接実行する。
    - 実行: backend（dry-run / webhook / pseudo）に委譲（support_actions.py）
    """
    log = emit_log or print
    if identity_verifier is not None:
        result = identity_verifier.verify(identity)
        status = "確認済み" if result.verified else "未確認"
        log(f"   [action] 本人確認（{result.method}）: {status} — {result.detail}")
        if not result.verified:
            return (f"本人確認が完了しないため '{action.action_type}' は実行せず、"
                    "有人対応へ引き継ぎます")

    # intervention.py: 副作用のあるアクションのみ、実行前に人間の承認（CONFIRM）を求める。
    # escalate_to_human 等の承認不要アクションは待たせず直接実行する（引き継ぎの取りこぼし防止）。
    if action.requires_confirmation:
        decision = ActionDecision(
            level=InterventionLevel.CONFIRM,
            confidence_score=0.5,
            reason=f"アクション実行前の確認: {action.action_type}",
        )
        response = handler.handle(decision)
        if not response.should_continue:
            if response.timeout_reached:
                return (f"承認待ちがタイムアウトしたため '{action.action_type}' は実行せず、"
                        "有人対応へエスカレーションします")
            return f"アクション '{action.action_type}' はキャンセルされました"

    outcome = backend.execute(action.action_type, action.args)
    return outcome.message


# =============================================================================
# コアパイプライン（イベント発行型）
# =============================================================================

def run_support_agent_core(
    query: str = DEFAULT_QUERY,
    verbose: bool = False,
    use_web: bool = True,
    do_action: bool = True,
    dry_run: bool = True,
    vertical: Optional[str] = None,
    identity: Optional[Dict[str, str]] = None,
    emit: Optional[EmitFn] = None,
    confirm: Optional[ConfirmFn] = None,
) -> Optional[SupportResult]:
    """GRACE-Support パイプラインを実行する（CLI 版 `run_support_agent` と同等）。

    Args:
        emit: 進捗イベントのコールバック（None なら通知なし）
        confirm: HITL CONFIRM/ESCALATE の解決コールバック。
            None の場合は自動承認（CLI 互換。既定ドライランのため安全）。
            Web からは必ず InterventionBridge.resolver を渡すこと。
    """
    _emit: EmitFn = emit or (lambda _e: None)

    def log(message: str, step: Optional[str] = None, **data) -> None:
        _emit(SupportEvent(type="log", step=step, message=message, data=data))

    def step_started(step: str, title: str, **data) -> None:
        _emit(SupportEvent(type="step", step=step, status="started", title=title, data=data))

    def step_finished(step: str, **data) -> None:
        _emit(SupportEvent(type="step", step=step, status="finished", data=data))

    def step_skipped(step: str, **data) -> None:
        _emit(SupportEvent(type="step", step=step, status="skipped", data=data))

    # 0. APIキーの存在チェック（未設定だと LLM 呼び出しで失敗する）
    if not os.getenv("ANTHROPIC_API_KEY"):
        _emit(SupportEvent(
            type="error",
            message="⚠️ ANTHROPIC_API_KEY が未設定です。.env に設定してください。",
        ))
        return None

    # ⚠️ get_config() はプロセス共有のシングルトンを返す。この関数は後段で
    # config.qdrant.allowed_collections / config.llm.prompt_addendum を
    # 業界プロファイルに合わせて書き換えるため、シングルトンをそのまま使うと
    # jobs.py がジョブごとに立てるワーカースレッド同士で値を奪い合う
    # （gov のリクエストが ec の検索スコープで走る等）。
    # リクエスト単位のディープコピーを作り、以降の生成物（planner/executor/
    # tools/verifier …）はすべてこのコピーを参照させる。
    config = copy.deepcopy(get_config())
    tool_registry = create_tool_registry(config)
    planner = create_planner(config)
    executor = create_executor(config, tool_registry)
    verifier = create_groundedness_verifier(config)
    agreement_calc = create_source_agreement_calculator(config)
    resolve_confirm: ConfirmFn = confirm or (lambda _req: AUTO_PROCEED)
    handler = create_intervention_handler(
        config,
        on_notify=lambda msg: log(f"   [intervention/notify] {msg}", step="action"),
        on_confirm=resolve_confirm,
        on_escalate=resolve_confirm,
    )
    th = config.confidence.thresholds

    # 意図分類器（二段判定の第 2 段）: キーワード候補が一致したときだけ呼ばれる。
    # 同一クエリへの分類は 1 回で済むようメモ化する（エスカレ判定とアクション判定で共有）。
    _raw_classify = create_intent_classifier(config)
    _intent_cache: Dict[str, Optional[Intent]] = {}

    def classify(q: str) -> Optional[Intent]:
        if q not in _intent_cache:
            _intent_cache[q] = _raw_classify(q)
            log(f"  [intent] 意図分類（{INTENT_MODEL}）: {_intent_cache[q] or '不明'}",
                step="gate", intent=_intent_cache[q])
        return _intent_cache[q]

    # 「情報なし回答」判定器（④' ゲートの第 2 段）: 候補句が一致したときだけ呼ばれる
    _raw_no_info_judge = create_no_info_judge(config)

    def no_info_judge(q: str, a: str) -> Optional[bool]:
        verdict = _raw_no_info_judge(q, a)
        label = {True: "no_info", False: "answered", None: "判定失敗"}[verdict]
        log(f"  [no-info] 実質回答判定（{INTENT_MODEL}）: {label}",
            step="no_info", verdict=label)
        return verdict

    # 業界プロファイル（--vertical）: しきい値・エスカレ語・アクション対応・本人確認を切り替え
    profile = PROFILES.get(vertical) if vertical else None
    notify_th = profile.notify_th if (profile and profile.notify_th is not None) else th.notify
    confirm_th = profile.confirm_th if (profile and profile.confirm_th is not None) else th.confirm

    # コアへの配線: 検索スコープ（rag_search の許可リスト）と業界方針（reasoning へ注入）。
    # tools は config への参照を保持しているため、ここでの設定が実行時に効く。
    # 注入する方針は build_prompt_addendum()（業界固有の方針＋共通の SCOPE_POLICY）。
    # 検索スコープが効くのは内部 RAG だけで、Web 検索にはドメイン制限が無いため、
    # 担当範囲外の話題を「回答しない」ことは生成側で担保する（verticals.SCOPE_POLICY）。
    # W-1: Web 検索は優先ドメインの「加点」でスコープを補強する（除外はしない。
    # 絞り込むと 0 件化 → 情報なし回答 → ④' の誤エスカレへ連鎖するため）。
    config.qdrant.allowed_collections = list(profile.collections) if profile else []
    config.llm.prompt_addendum = profile.build_prompt_addendum() if profile else ""
    config.web_search.preferred_domains = list(profile.preferred_domains) if profile else []

    if profile is not None:
        step_started(
            "profile", f"業界プロファイル: {profile.name}（--vertical {vertical}）",
            vertical=vertical, name=profile.name,
        )
        log(f"  検索スコープ: {', '.join(profile.collections) or '—'}"
            "（未登録コレクションは自動的に無視）", step="profile")
        log(f"  しきい値: notify={notify_th} / confirm={confirm_th} / 本人確認={profile.require_identity}",
            step="profile")
        if profile.prompt_addendum:
            log(f"  方針(reasoningへ注入): {profile.prompt_addendum}", step="profile")
        log("  スコープ方針: 担当範囲外の話題は回答せず窓口を案内（Web 検索は"
            "ドメイン制限が無いため生成側で担保）", step="profile")
        if profile.preferred_domains:
            log(f"  Web優先ドメイン: {', '.join(profile.preferred_domains)}"
                "（加点のみ・非一致も残す）", step="profile")
        step_finished(
            "profile",
            vertical=vertical, name=profile.name,
            collections=list(profile.collections),
            notify_th=notify_th, confirm_th=confirm_th,
            require_identity=profile.require_identity,
            prompt_addendum=profile.prompt_addendum,
        )
    else:
        step_skipped("profile")

    # ① Plan
    step_started("plan", "① Plan（planner）")
    log(f"❓ 問い合わせ: {query}", step="plan")
    plan = planner.create_plan(query)
    log(f"  [plan] {len(plan.steps)} ステップ (complexity={plan.complexity:.2f})", step="plan")
    step_finished("plan", steps=len(plan.steps), complexity=plan.complexity)

    # ② Execute（内部 RAG → reasoning）
    step_started("execute", "② Execute（executor + tools: 内部RAG）")
    result = executor.execute(plan)
    internal_answer = result.final_answer or ""
    internal_citations = _collect_citations(result.step_results)
    # executor が動的挿入した web_search（RAG スコア不足時）の使用を検知
    used_dynamic_web = any(c.startswith("[Web]") for c in internal_citations)
    for sr in result.step_results:
        log(f"  step{sr.step_id}: {sr.status} (sources={len(sr.sources)})", step="execute")
    if used_dynamic_web:
        log("  [web] executor が動的 Web 検索を使用（RAG スコア不足のため）", step="execute")
    step_finished(
        "execute",
        steps=[{"step_id": sr.step_id, "status": str(sr.status), "sources": len(sr.sources)}
               for sr in result.step_results],
        used_dynamic_web=used_dynamic_web,
        citations=len(internal_citations),
    )

    # ③ 根拠評価（内部）
    #
    # 検証器には**出典本文**を渡す。出典識別子（ファイル名）だけを渡すと
    # 「情報源: gov_faq.csv」のようになり、どの主張も裏付けられず全て neutral
    # （支持率の分母 0）になって不当に escalate へ倒れてしまう。
    # 本文が取れない経路（legacy agent 等）では従来どおり出典ラベルで代替する。
    # ⑤ の Web 側が `_web_source_texts` で本文を渡しているのと同じ扱いに揃える。
    step_started("confidence", "③ Confidence（GroundednessVerifier: 内部回答の裏付け）")
    internal_source_texts = _collect_source_texts(result.step_results)
    verify_sources = internal_source_texts or [_citation_text(c) for c in internal_citations]
    gres = verifier.verify(query, internal_answer, verify_sources)
    if verbose:
        log(f"  [groundedness] 検証ソース={len(verify_sources)} 件"
            f"（{'本文' if internal_source_texts else '出典ラベル(fallback)'}）", step="confidence")
        log(f"  [groundedness] supported={gres.supported} / total={gres.total} / "
            f"contradiction={gres.has_contradiction} / verified={gres.verified}", step="confidence")
    log(f"  [groundedness] 支持率={gres.support_rate:.2f}"
        f"（判定可能 {gres.supported + gres.contradicted}/{gres.total} 主張）"
        f" / 出典数={len(internal_citations)}", step="confidence")
    step_finished(
        "confidence",
        support_rate=gres.support_rate,
        supported=gres.supported, contradicted=gres.contradicted, total=gres.total,
        verified=gres.verified, has_contradiction=gres.has_contradiction,
        citations=len(internal_citations),
    )

    # ④ 回答ゲート（内部）＋ プロファイルのエスカレ語による強制エスカレ
    step_started("gate", "④ 回答ゲート（notify/confirm しきい値＋強制エスカレ＋救済）")
    decision, warning = _answer_gate(
        gres.support_rate, gres.verified, len(internal_citations), notify_th, confirm_th
    )
    forced_escalate, matched_kw, _intent = _should_force_escalate(query, profile, classify)
    if forced_escalate:
        decision, warning = "escalate", False
        log(f"  [profile] エスカレ語 '{matched_kw}'（意図={_intent or '不明'}）を検知 → "
            f"有人対応へ（{profile.name}）", step="gate")
    elif matched_kw is not None:
        log(f"  [profile] エスカレ語候補 '{matched_kw}' は FAQ 質問（意図=question）→ "
            "誤検知抑止・通常フローを継続", step="gate")

    # ④-救済: 出典付き・非「情報なし」・矛盾なしの内部回答が、groundedness を
    # 「肯定できなかった」というだけで escalate に落ち、⑤ の Web 二次生成で
    # 「情報なし」回答に化けて ④' で誤エスカレするのを防ぐ（ec「返金ポリシー」で
    # 顕在化）。範囲外の「情報なし」回答は除外され従来どおり escalate（saas 等）。
    rescued = False
    if _should_rescue_unaffirmed(
        decision, forced_escalate, gres.has_contradiction,
        len(internal_citations), internal_answer, query, no_info_judge,
    ):
        decision, warning = "answer", True
        rescued = True
        log("  [gate] groundedness の裏付けは弱いが矛盾なし・出典付きの実質回答 → "
            "answer（未確認注記）として維持し、無駄な Web 二次生成・誤エスカレを回避", step="gate")
    step_finished(
        "gate",
        decision=decision, warning=warning,
        forced_escalate=forced_escalate, matched_keyword=matched_kw,
        intent=_intent, rescued=rescued,
        notify_th=notify_th, confirm_th=confirm_th,
    )

    support = SupportResult(
        answer=internal_answer,
        citations=internal_citations,
        groundedness=gres.support_rate,
        groundedness_decided=gres.supported + gres.contradicted,
        decision=decision,
        warning=warning,
        used_web=used_dynamic_web,
        vertical=vertical,
        overall_confidence=result.overall_confidence,
    )

    # ⑤ Web フォールバック（内部が escalate かつ 強制エスカレでない場合のみ・v2）
    #
    # executor が動的 Web 検索を使用済みの場合、内部回答は既に同一クエリの
    # Web 結果から生成されている。内部ゲートで escalate になる主因は
    # groundedness 検証が出典ラベル（URL 文字列）にしか当たらないことなので、
    # 回答を作り直す（reasoning 再実行）のではなく、内部回答を本文スニペットで
    # **再検証だけ**行う（重複していた Web 検索→推論の 2 周目を省略。
    # 1 ケースあたり十数秒〜の短縮）。
    if decision == "escalate" and use_web and not forced_escalate:
        step_started("web", "⑤ Web フォールバック（tools.web_search → reasoning → 相互検証）")
        reuse_internal = used_dynamic_web and bool(internal_answer)
        if reuse_internal:
            log("  executor が同一クエリで Web 検索済み → 内部回答を再利用し、"
                "本文スニペットで再検証のみ実施（重複推論を省略）", step="web")
        else:
            log("  内部ナレッジの根拠が不足 → Web で裏取りを試みます", step="web")
        web_res = tool_registry.execute("web_search", query=query)
        web_output = web_res.output if (web_res and web_res.success) else None

        if web_output:
            if reuse_internal:
                web_answer = internal_answer
            else:
                web_reason = tool_registry.execute("reasoning", query=query, sources=web_output)
                web_answer = (web_reason.output or "") if (web_reason and web_reason.success) else ""
            web_citations = _web_citations(web_output)
            log(f"  [web] {len(web_citations)} 件の出典を取得", step="web")

            gres_web = verifier.verify(query, web_answer, _web_source_texts(web_output))
            agreement: Optional[float] = None
            contradiction = gres_web.has_contradiction
            # 相互検証は「独立に生成した 2 つの回答」の比較。再利用時は
            # 同一回答の比較になり無意味（常に一致）なのでスキップする。
            if not reuse_internal and internal_answer and web_answer:
                agreement = agreement_calc.calculate([internal_answer, web_answer])
                if agreement < confirm_th:
                    contradiction = True
                log(f"  [相互検証] 内部×Web 一致度={agreement:.2f} / 矛盾={contradiction}", step="web")

            w_decision, w_warning = _answer_gate(
                gres_web.support_rate, gres_web.verified, len(web_citations),
                notify_th, confirm_th,
            )
            g_rate, g_decided = _pick_groundedness(gres, gres_web)
            support = SupportResult(
                answer=web_answer if w_decision == "answer" else internal_answer,
                citations=_merge_citations(internal_citations, web_citations),
                groundedness=g_rate,
                groundedness_decided=g_decided,
                decision=w_decision,
                warning=w_warning,
                used_web=True,
                web_reused=reuse_internal,
                source_agreement=agreement,
                contradiction=contradiction,
                vertical=vertical,
                overall_confidence=result.overall_confidence,
            )
            step_finished(
                "web",
                web_reused=reuse_internal, citations=len(web_citations),
                decision=w_decision, warning=w_warning,
                support_rate=gres_web.support_rate,
                agreement=agreement, contradiction=contradiction,
            )
        else:
            log("  [web] 有効な検索結果が得られませんでした", step="web")
            support.used_web = True
            step_finished("web", web_reused=False, citations=0, decision=support.decision)
    else:
        step_skipped("web", reason="内部回答で確定" if decision == "answer" else
                     ("強制エスカレ" if forced_escalate else "Web フォールバック無効"))

    # ④' 「情報なし回答」検知ゲート（docs/vertical_spec_review.md の残課題①）:
    # 誠実な「見つかりませんでした」型の回答は出典・支持率を伴ってゲートを
    # answer で通過してしまう（範囲外質問で顕在化）。二段判定で実質回答か
    # を確かめ、情報なしなら有人対応へ倒す。
    if support.decision == "answer" and support.answer:
        step_started("no_info", "④' 情報なし回答検知ゲート（定型句候補→軽量LLM の二段判定）")
        # 出典が Web のみ（社内コレクション根拠ゼロ）の回答は、候補句がなくても
        # ④' 判定を必須にする（out-of-scope × 動的 Web 検索の answer 化対策）
        web_only = bool(support.citations) and all(
            c.startswith("[Web]") for c in support.citations
        )
        no_info, marker = _detect_no_info_answer(
            query, support.answer, no_info_judge, force_judge=web_only,
        )
        if no_info:
            trigger = f"候補句 '{marker}'" if marker is not None else "出典が Web のみ"
            log(f"  [gate] 情報なし回答を検知（{trigger}）→ 有人対応へエスカレーション", step="no_info")
            support.decision = "escalate"
            support.warning = False
            support.no_info_detected = True
        elif marker is not None or web_only:
            trigger = f"情報なし候補句 '{marker}' はあるが" if marker is not None else "出典が Web のみだが"
            log(f"  [gate] {trigger}実質回答（answered）→ 回答を維持", step="no_info")
        step_finished("no_info", no_info=no_info, marker=marker, web_only=web_only)
    else:
        step_skipped("no_info")

    # ⑥ アクション（v3）: 本人確認 → HITL（CONFIRM）→ バックエンド実行
    action_done = False
    if do_action:
        action = _decide_action(query, support.decision, profile, classify)
        if action is not None:
            backend = create_action_backend(dry_run=dry_run)
            require_identity = bool(profile and profile.require_identity)
            step_started(
                "action",
                f"⑥ Action（本人確認 → intervention CONFIRM → ActionTool[{backend.name}]）",
                action_type=action.action_type,
                args=action.args,
                requires_confirmation=action.requires_confirmation,
                backend=backend.name,
                dry_run=dry_run,
                require_identity=require_identity,
            )
            log(f"  [action] 種別={action.action_type}（要承認={action.requires_confirmation}）",
                step="action")
            support.action = action
            identity_verifier = (
                create_identity_verifier(dry_run=dry_run) if require_identity else None
            )
            support.action_result = _perform_action(
                action, handler, backend,
                identity_verifier=identity_verifier, identity=identity,
                emit_log=lambda msg: log(msg, step="action"),
            )
            support.identity_checked = require_identity
            log(f"  [action] {support.action_result}", step="action")
            step_finished(
                "action",
                action_type=action.action_type,
                backend=backend.name,
                dry_run=dry_run,
                identity_checked=require_identity,
                result_message=support.action_result,
            )
            action_done = True
    if not action_done:
        step_skipped("action")

    # KPI 計測用メタデータ（eval/vertical が参照）
    support.forced_escalate = forced_escalate
    support.intent = _intent_cache.get(query)

    _emit(SupportEvent(type="result", data=result_to_dict(support)))
    return support
