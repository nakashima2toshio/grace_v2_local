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
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from backend.app.core.gates import (
    JUDGE_DISABLED,
    _answer_gate,
    _citation_text,
    _collect_citations,
    _collect_source_texts,
    _contradicted_claims,
    _decide_action,
    _detect_no_info_answer,
    _merge_citations,
    _pick_groundedness,
    _should_force_escalate,
    _should_rescue_unaffirmed,
    _should_rescue_unverified,
    _web_citations,
    _web_source_texts,
    create_cluster_analyzer,
    create_intent_classifier,
    create_no_info_judge,
    create_scope_classifier,
    deferred_main_questions,
    detect_question_clusters,
    judge_model,
    looks_like_multi_question,
    reconstruct_query,
    split_by_scope,
)
from backend.app.core.verticals import (
    DEFAULT_QUERY,
    PROFILES,
    ActionRequest,
    Decision,
    Intent,
)
from config import get_selectable_ollama_models
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
    "analyze",     # 0-(A) 入力・質問分析（複数質問の検知 → 選択 → 再構成）
    "profile",     # 0-(B) 業界プロファイル適用（--vertical 指定時のみ）
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
class QuestionCluster:
    """1 つの主質問と、それに従属する関連質問のまとまり。

    複数質問クエリの採用単位。**主質問だけを採用単位にしてはいけない。**
    関連質問は主質問に従属しており（例:「住民票の取り方は？ **その手数料は？**」の
    「その手数料」）、切り離すと主質問の回答自体が不完全になる。
    設計: docs/multi_question_handling.md §13.3
    """

    main: str                                        # 主質問（独立したトピック）
    related: List[str] = field(default_factory=list)  # 主質問に従属する関連質問


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
    model_used: str = ""                      # このリクエストで実際に使われた LLM（config.llm.model）

    # --- 複数質問クエリ（docs/multi_question_handling.md §13.5）---------------
    # ⚠️ すべて optional。単一質問では既定値のままで、旧フロント・既存 API
    #    クライアントの挙動は変わらない。
    is_multi_question: bool = False                   # 複数質問と判定されたか
    question_clusters: List[QuestionCluster] = field(default_factory=list)
    adopted_cluster_index: Optional[int] = None       # 採用したクラスタの位置
    # 再構成後の質問文。**原文とは別に保持する。**
    # 再構成は LLM が行うため誤りうる。利用者が「何を質問として解釈されたか」を
    # 検証できるよう、UI へ出す前提で原文を潰さずに持つ（§13.5）。
    reconstructed_query: Optional[str] = None
    # 🔴 採用しなかった主質問。**必ず返すこと。**
    #    これを返さないと「片方が無言で落ち、しかも support_rate が高いため
    #    高信頼として提示される」という最も危険な事故（§概要）と区別がつかない。
    deferred_questions: List[str] = field(default_factory=list)
    # 担当範囲外と判定した主質問と、それに添える窓口案内。
    # ⚠️ `deferred_questions` と混ぜない。保留は「範囲内だが今回は答えていない」、
    #    こちらは「範囲外なので答えない」で、利用者に伝えるべきことが違う。
    out_of_scope_questions: List[str] = field(default_factory=list)
    out_of_scope_guidance: str = ""


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
    model: Optional[str] = None,
    identity: Optional[Dict[str, str]] = None,
    emit: Optional[EmitFn] = None,
    confirm: Optional[ConfirmFn] = None,
) -> Optional[SupportResult]:
    """GRACE-Support パイプラインを実行する（CLI 版 `run_support_agent` と同等）。

    Args:
        model: 使用する LLM。None（既定）なら config/grace_config.yml の
            llm.model / llm.light_model のまま。指定する場合は
            `config.get_selectable_ollama_models()` に含まれる値のみ許可する
            （Anthropic 系・tool calling 非対応は選ばせない）。
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

    # 0. LLM の事前チェックは行わない。
    #    LLM はローカル（Ollama）実行のため API キーが存在しない。Embedding
    #    （Gemini）のキーは検索経路で個別に扱う。疎通不良は各 LLM 呼び出し側の
    #    例外処理でフォールバックする。

    # ⚠️ get_config() はプロセス共有のシングルトンを返す。この関数は後段で
    # config.qdrant.allowed_collections / config.llm.prompt_addendum を
    # 業界プロファイルに合わせて書き換えるため、シングルトンをそのまま使うと
    # jobs.py がジョブごとに立てるワーカースレッド同士で値を奪い合う
    # （gov のリクエストが ec の検索スコープで走る等）。
    # リクエスト単位のディープコピーを作り、以降の生成物（planner/executor/
    # tools/verifier …）はすべてこのコピーを参照させる。
    config = copy.deepcopy(get_config())

    # UI（3タブ共通のモデルセレクタ）からの上書き。model / light_model の
    # 両方を揃える — judge_model()（意図分類・情報なし判定）は light_model を
    # 読むため、model だけ上書きすると判定系だけ既定モデルのまま食い違う
    # （backend/tests/test_judge_model_resolution.py が守っている問題と同種）。
    # heavy_model は触らない："" のときは model へ自動フォールバックする
    # 既存ロジック（resolve_heavy_model）により、これも選択したモデルに揃う。
    if model:
        if model not in get_selectable_ollama_models():
            raise ValueError(
                f"未対応のモデルです: {model}（選択可能: "
                f"{', '.join(get_selectable_ollama_models())}）"
            )
        config.llm.model = model
        config.llm.light_model = model

    tool_registry = create_tool_registry(config)
    planner = create_planner(config)
    executor = create_executor(config, tool_registry)
    # ⚠️ **executor と同じ検証器インスタンスを使う。**
    #
    # executor は実行の最後に `_blend_groundedness_confidence` の中で、
    # 同じ回答・同じソースを検証している。別インスタンスを立てると、直後の
    # ③ 根拠評価がまったく同じ判定をもう一度 LLM へ投げることになる
    # （実測: 27.3 秒 + 19.9 秒 = リクエスト全体 2:00 の 39%）。
    #
    # インスタンスを共有すると `GroundednessVerifier` 内のメモが効き、2 回目は
    # LLM を呼ばずに同じ結果を返す。入力が異なる場合（⑤ の Web 回答検証）は
    # 従来どおり検証される。executor が検証器を持たない実装に差し替わっても
    # 動くよう、getattr でフォールバックする。
    verifier = getattr(executor, "groundedness_verifier", None) or \
        create_groundedness_verifier(config)
    agreement_calc = create_source_agreement_calculator(config)
    resolve_confirm: ConfirmFn = confirm or (lambda _req: AUTO_PROCEED)
    handler = create_intervention_handler(
        config,
        on_notify=lambda msg: log(f"   [intervention/notify] {msg}", step="action"),
        on_confirm=resolve_confirm,
        on_escalate=resolve_confirm,
    )
    th = config.confidence.thresholds

    # 判定系（意図分類・情報なし判定）が実際に使うモデル名。
    # ⚠️ `INTENT_MODEL` をそのままログに出さない。あれは環境変数だけを見る
    # モジュール定数で、config（yml）経由で解決される実体と食い違いうる
    # （実測 2026-08-17 02:12: 表示 gemma4:e4b / 他コンポーネントは
    # gemma4-e4b-ctx8k）。表示と実体がずれると原因調査が空振りする。
    _judge_model = judge_model(config)

    # 意図分類器（二段判定の第 2 段）: キーワード候補が一致したときだけ呼ばれる。
    # 同一クエリへの分類は 1 回で済むようメモ化する（エスカレ判定とアクション判定で共有）。
    _raw_classify = create_intent_classifier(config)
    _intent_cache: Dict[str, Optional[Intent]] = {}

    def classify(q: str) -> Optional[Intent]:
        if q not in _intent_cache:
            _intent_cache[q] = _raw_classify(q)
            log(f"  [intent] 意図分類（{_judge_model}）: {_intent_cache[q] or '不明'}",
                step="gate", intent=_intent_cache[q])
        return _intent_cache[q]

    # 「情報なし回答」判定器（④' ゲートの第 2 段）: 候補句が一致したときだけ呼ばれる
    #
    # ⚠️ **判定失敗の理由を実行記録に残す。** 判定器は理由を stderr へ出すだけ
    # だったため、emit 経由の実行ログ（UI・SSE）には `判定失敗` という結果しか
    # 残らず、原因を後から追えなかった（実測「明日の東京の天気は？」）。
    # 出典が Web のみの回答は判定が必須（force_judge=True）で、判定失敗は
    # 安全側の escalate に倒れる。判定器が失敗し続けていると Web フォールバック
    # の回答が内容によらず全件エスカレするため、理由が分からないと
    # 「安全側に倒れた」のか「判定器が壊れている」のかを区別できない。
    _judge_failure: Dict[str, Optional[str]] = {"kind": None, "detail": None}

    def _record_judge_failure(kind: str, detail: str) -> None:
        _judge_failure["kind"] = kind
        _judge_failure["detail"] = detail

    _raw_no_info_judge = create_no_info_judge(
        config, on_failure=_record_judge_failure
    )

    # 第 2 段が実際に呼ばれたか／その判定は何だったか。④' のログを正確にするために
    # **判定そのもの**を見る（失敗コールバックの有無から推測しない）。
    _last_verdict: Dict[str, Any] = {"called": False, "value": None}

    def no_info_judge(q: str, a: str) -> Optional[bool]:
        _judge_failure["kind"] = _judge_failure["detail"] = None
        verdict = _raw_no_info_judge(q, a)
        _last_verdict["called"] = True
        _last_verdict["value"] = verdict
        kind, detail = _judge_failure["kind"], _judge_failure["detail"]
        if verdict is True:
            label = "no_info"
        elif verdict is False:
            label = "answered"
        else:
            # 「無効（そもそも実行していない）」を「失敗（実行して駄目だった）」と
            # 同じ文言にしない。どちらも None を返すため結果だけでは区別できない。
            label = "判定なし" if kind == JUDGE_DISABLED else "判定失敗"
        suffix = f"（{detail}）" if verdict is None and detail else ""
        log(f"  [no-info] 実質回答判定（{_judge_model}）: {label}{suffix}",
            step="no_info", verdict=label,
            failure_kind=kind, failure_detail=detail)
        return verdict

    # 業界プロファイルの解決。
    # ⚠️ **適用（0-(B)）より先に解決する。** 0-(A) のスコープ判定が
    # `scope_description` / `out_of_scope_guidance` を読むため。
    # config への注入（検索スコープ・方針）は従来どおり 0-(B) で行う。
    profile = PROFILES.get(vertical) if vertical else None

    # =========================================================================
    # 0-(A) 入力・質問分析（複数質問の検知 → 主質問の選択 → 再構成）
    # =========================================================================
    #
    # 設計: docs/multi_question_handling.md §13（絞り込み方式）。
    #
    # ここは**前処理**であり、パイプライン本体（planner/executor/gates）の判定
    # ロジックは一切変えない。再構成後の文を `query` として渡すため、planner から
    # 見ればそれが「利用者の元の質問文」であり、完全一致でコピーする規則
    # （grace/planner.py:110-111）とも衝突しない。
    #
    # ⚠️ **安全側の向きが後段のゲートと逆である。** ④・④' が「判定できないなら
    # escalate（答えない）」に倒すのに対し、ここは「判定できないなら**単一質問**」
    # に倒す。誤って分解する方が害が大きく、何もしなければ現行動作そのものだから。
    # 第 1 段（接続表現・疑問符の数）で弾かれれば LLM は 1 度も呼ばれない。
    original_query = query
    question_clusters: List[QuestionCluster] = []
    adopted_cluster_index: Optional[int] = None
    reconstructed_query: Optional[str] = None
    deferred_questions: List[str] = []
    out_of_scope_questions: List[str] = []

    analyze_settled = False   # analyze ステップの決着イベントを出したか
    # ⚠️ **第 1 段が一致してから解析器を作る。** `create_cluster_analyzer()` は
    # 生成時点で LLM クライアントを組み立てるため、引数の位置で無条件に呼ぶと
    # 単一質問のリクエストでも毎回クライアントを作ることになる（第 1 段で LLM を
    # 呼ばずに弾く、という二段判定の狙いが半分崩れる）。
    clusters = (
        detect_question_clusters(query, create_cluster_analyzer(config))
        if looks_like_multi_question(query)
        else []
    )
    if clusters:
        step_started(
            "analyze", f"0-(A) 入力・質問分析（複数質問を検知: 主質問 {len(clusters)} 件）",
            clusters=[{"main": m, "related": list(r)} for m, r in clusters],
        )
        question_clusters = [QuestionCluster(main=m, related=list(r)) for m, r in clusters]
        for i, (main, related) in enumerate(clusters):
            suffix = f"（関連: {' / '.join(related)}）" if related else ""
            log(f"  [multi-q] 主質問{i + 1}: {main}{suffix}", step="analyze")

        # --- 担当範囲で分ける -------------------------------------------------
        #
        # 範囲外の主質問は**選択肢に出さない**。選ばせても答えは変わらず
        # （生成側の SCOPE_POLICY が断る）、利用者に無駄な 1 往復を強いるだけで、
        # しかも選ばれなかった側は「保留（未回答）」として落ちる。
        # 範囲外は保留ではなく「断って窓口案内」で返す（実測 2026-08-29 の
        # 「住民票 ＋ 明日の天気」で顕在化）。
        #
        # 判定できないときは全件を範囲内として扱う（＝従来どおり選択を出す）。
        # 範囲外と誤判定して答えられる質問を断つほうが害が大きい。
        in_scope_idx, out_scope_idx = split_by_scope(
            clusters, create_scope_classifier(config, profile)
        )
        out_of_scope_questions = [clusters[i][0] for i in out_scope_idx]
        if out_of_scope_questions:
            log(f"  [multi-q] 担当範囲外（{profile.name if profile else '-'}）: "
                f"{' / '.join(out_of_scope_questions)} → 選択肢に出さず、"
                "断り＋窓口案内で返します", step="analyze")

        if len(in_scope_idx) == 1:
            # 範囲内が 1 つだけ（関連質問の有無を問わず）→ 選ぶ余地が無い。
            adopted_cluster_index = in_scope_idx[0]
        else:
            # 範囲内の主質問が複数 → 利用者に選ばせる（自動選定はしない・§13.1）。
            # CLI（confirm 未指定）は AUTO_PROCEED が selected_option を持たない
            # ため、後段のフォールバックで先頭クラスタが採用される。
            options = [clusters[i][0] for i in in_scope_idx]
            selection = resolve_confirm(InterventionRequest(
                level=InterventionLevel.CONFIRM,
                message="複数の質問が含まれています。先に回答する質問を選んでください。",
                question="どの質問に回答しますか？",
                reason="multi_question_selection",
                options=options,
                timeout_seconds=config.intervention.default_timeout,
            ))
            if not selection.should_continue:
                # 拒否・タイムアウト → **現行どおり単一質問として処理する**（§13.8-7）。
                # ここで escalate に倒さないのは、分析は前処理でありゲートではないため。
                # 選択できなかったことを理由に回答自体を諦めるのは過剰。
                reason = "タイムアウト" if selection.timeout_reached else "選択なし"
                log(f"  [multi-q] 主質問の選択が得られませんでした（{reason}）→ "
                    "原文のまま単一質問として処理します", step="analyze")
                question_clusters = []
                clusters = []
                out_of_scope_questions = []
                step_finished("analyze", is_multi_question=False, reason=reason)
                analyze_settled = True
            else:
                chosen = selection.selected_option
                # options は範囲内クラスタだけなので、元のクラスタ添字へ戻す。
                adopted_cluster_index = (
                    in_scope_idx[options.index(chosen)] if chosen in options
                    else in_scope_idx[0]
                )
                if chosen not in options:
                    # 選択肢に無い値が返った（CLI の自動承認・想定外の入力）。
                    # 先頭を採用し、残りは保留として必ず提示する（黙って落とさない）。
                    log("  [multi-q] 選択が特定できないため先頭の主質問を採用します",
                        step="analyze")

    if clusters and adopted_cluster_index is not None:
        main, related = clusters[adopted_cluster_index]
        reconstructed_query = reconstruct_query(main, related, config)
        # 🔴 保留 = **範囲内なのに今回は答えなかった**主質問だけ。
        # 範囲外は「保留（あとで答えます）」ではなく「担当範囲外です」なので混ぜない。
        deferred_questions = [
            q for q in deferred_main_questions(clusters, adopted_cluster_index)
            if q not in out_of_scope_questions
        ]
        log(f"  [multi-q] 採用: {main}", step="analyze")
        if reconstructed_query != original_query:
            log(f"  [multi-q] 再構成後のクエリ: {reconstructed_query}", step="analyze")
        if deferred_questions:
            # 🔴 保留した主質問は必ず出す。出さないと「片方が無言で落ちたのに
            #    支持率が高いので高信頼として提示される」事故と区別がつかない。
            log(f"  [multi-q] 保留した主質問: {' / '.join(deferred_questions)}",
                step="analyze")
        # 以降のパイプラインは再構成後のクエリで走る。原文は original_query に残す。
        query = reconstructed_query
        step_finished(
            "analyze",
            is_multi_question=True,
            adopted_cluster_index=adopted_cluster_index,
            reconstructed_query=reconstructed_query,
            deferred_questions=deferred_questions,
            out_of_scope_questions=out_of_scope_questions,
        )
    elif not analyze_settled:
        # 第 1 段で弾かれた／解析器が単一と判断した＝現行フローそのもの。
        step_skipped("analyze")

    # 業界プロファイル（--vertical）: しきい値・エスカレ語・アクション対応・本人確認を切り替え
    # （プロファイルの**解決**は 0-(A) の手前で済ませてある。適用はここから）
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
    # ⚠️ **担当範囲外の質問を生成側へ渡す。** 0-(A) はそれらを検索クエリから
    # 外している（外さないと検索の重心がボケる）。外したままだと生成側は
    # 範囲外の質問があったことすら知らず、利用者から見て「聞いたはずの片方が
    # 返答に出てこない」状態になる。検索は絞ったまま、質問文だけを渡して
    # **同じ回答の中で**断り＋窓口案内をさせる。
    config.llm.prompt_addendum = (
        profile.build_prompt_addendum(out_of_scope_questions) if profile else ""
    )
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
        if out_of_scope_questions:
            log(f"  範囲外の質問を回答内で断るよう注入: "
                f"{' / '.join(out_of_scope_questions)}", step="profile",
                out_of_scope_questions=list(out_of_scope_questions))
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
            out_of_scope_questions=list(out_of_scope_questions),
            # ⚠️ **実際に注入した文字列**。`prompt_addendum` は業界固有の方針
            # だけで、SCOPE_POLICY も範囲外質問の指示も含まない。何が生成側へ
            # 渡ったかは、こちらを見ないと分からない。
            injected_prompt_addendum=config.llm.prompt_addendum,
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
    # ⚠️ **矛盾と判定された主張は本文つきで出す。** 矛盾が 1 件でもあると
    # executor が answer_conf を 0.30 に cap するため、誤検知だと正しい回答の
    # 信頼度が不当に下がる。件数（contradicted=1）だけでは誤検知か本物かを
    # 後から判断できない（実測「明日の東京の天気は？」で追跡不能だった）。
    contradicted_claims = _contradicted_claims(gres)
    for claim in contradicted_claims:
        log(f"  [groundedness] 矛盾と判定された主張: {claim}", step="confidence")
    step_finished(
        "confidence",
        support_rate=gres.support_rate,
        supported=gres.supported, contradicted=gres.contradicted, total=gres.total,
        verified=gres.verified, has_contradiction=gres.has_contradiction,
        citations=len(internal_citations),
        contradicted_claims=contradicted_claims,
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
            # ⑤-救済: 検証器**そのもの**が落ちた（例外・タイムアウト・空応答）
            # ことだけを理由に、生成に成功した回答を捨てない。ローカル LLM では
            # 検証 1 回に 90〜250 秒かかりタイムアウトが常態化するため、放置すると
            # 「答えを作れているのに answer=internal_answer（空）で escalate」に
            # なる（実測: 16:07:10 に 107 文字の正しい回答 → 16:11:43 検証
            # タイムアウト → 破棄）。矛盾なし・出典ありのときだけ未確認注記つきで
            # 維持する。救済後も後段 ④' の情報なし検知ゲートは必ず通る。
            w_rescued = _should_rescue_unverified(
                w_decision, gres_web.verification_failed, contradiction,
                len(web_citations), web_answer,
            )
            if w_rescued:
                w_decision, w_warning = "answer", True
                log("  [web] groundedness 検証器が判定不能（インフラ障害）→ "
                    "矛盾なし・出典ありのため回答を破棄せず未確認注記つきで維持",
                    step="web")
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
                verification_failed=gres_web.verification_failed,
                rescued=w_rescued,
            )
        else:
            log("  [web] 有効な検索結果が得られませんでした", step="web")
            support.used_web = True
            step_finished("web", web_reused=False, citations=0, decision=support.decision)
    else:
        # ⚠️ decision=="answer" を「内部回答で確定」と言い切らない。executor が
        # 動的 Web 検索へフォールバックしていると、確定した回答の出典は Web で
        # あって内部ナレッジではない（実測「明日の東京の天気は？」: RAG 0 件・
        # 出典 9 件すべて Web なのに「内部回答で確定」と表示された）。
        if decision == "answer":
            skip_reason = ("Web 検索結果で確定（executor が動的 Web 検索を実施済み）"
                           if used_dynamic_web else "内部回答で確定")
        elif forced_escalate:
            skip_reason = "強制エスカレ"
        else:
            skip_reason = "Web フォールバック無効"
        step_skipped("web", reason=skip_reason)

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
        # ⚠️ **「判定が得られなかった」を「answered と判定された」と書かない。**
        # 判定器は既定で無効（judges.enabled=false）なので、区別しないとログが嘘になる。
        judged = _last_verdict["called"]
        verdict_missing = judged and _last_verdict["value"] is None
        if no_info:
            trigger = f"候補句 '{marker}'" if marker is not None else "出典が Web のみ"
            log(f"  [gate] 情報なし回答を検知（{trigger}）→ 有人対応へエスカレーション", step="no_info")
            support.decision = "escalate"
            support.warning = False
            support.no_info_detected = True
        elif verdict_missing:
            # ここへ来るのは marker なし（＝ web_only だけがトリガ）のとき。
            # force_judge は「判定せよ」というトリガであって判定結果ではないので、
            # 判定が無いまま Web のみを理由にエスカレはしない。
            log("  [gate] 出典が Web のみだが第 2 段の判定が得られなかった "
                "→ Web のみを理由にはエスカレせず回答を維持", step="no_info")
        elif judged:
            trigger = f"情報なし候補句 '{marker}' はあるが" if marker is not None else "出典が Web のみだが"
            log(f"  [gate] {trigger}実質回答（answered）→ 回答を維持", step="no_info")
        step_finished("no_info", no_info=no_info, marker=marker, web_only=web_only,
                      verdict_missing=verdict_missing)
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
    # UI の「使用モデル」表示用。ヘッダーの GET /api/model はサーバー既定値の
    # 表示に過ぎず、model 引数で上書きした場合はここでしか実際の値が分からない。
    # ⚠️ テストの config スタブ（backend/tests/conftest.py 等）は
    # `llm=SimpleNamespace(prompt_addendum="")` のように core が触る属性のみを
    # 持つ最小構成のため、judge_model() と同じく getattr で欠落を許容する。
    support.model_used = getattr(getattr(config, "llm", None), "model", "") or ""

    # 0-(A) の結果。**両方の SupportResult 生成経路（内部確定・⑤ Web 経由）を
    # 通ったあとにここで一括で載せる。** 生成側に足すと片方だけ埋まる。
    # 単一質問では既定値のまま（is_multi_question=False / 空リスト）。
    support.is_multi_question = bool(question_clusters)
    support.question_clusters = question_clusters
    support.adopted_cluster_index = adopted_cluster_index
    # 🔴 再構成後クエリと保留質問は、複数質問だったときは必ず載せる。
    #    利用者が「何を質問として解釈され、何が保留されたか」を検証できないと、
    #    片方の質問が黙って落ちた状態と区別できない（§13.5）。
    support.reconstructed_query = (
        reconstructed_query if reconstructed_query != original_query else None
    )
    support.deferred_questions = deferred_questions
    # 範囲外と判定した主質問。断り＋窓口案内を UI が添える。
    support.out_of_scope_questions = out_of_scope_questions
    support.out_of_scope_guidance = (
        profile.out_of_scope_guidance if (profile and out_of_scope_questions) else ""
    )

    _emit(SupportEvent(type="result", data=result_to_dict(support)))
    return support
