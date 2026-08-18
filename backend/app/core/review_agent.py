# backend/app/core/review_agent.py
"""GRACE-Review コアサービス（文書レビュー・イベント発行型）。

設計: backend/docs/review_agent_spec.md §3。

Support（`support_agent.py`）が「問い合わせ → 回答」なのに対し、本モジュールは
**「文書 → 指摘」**と情報の流れが逆になる。それでも中核部品は無改造で機能する:

- `GroundednessVerifier` の「主張が出典で裏付けられるか」
  → 「**指摘が規程で裏付けられるか**」
- `_perform_action` / `ActionBackend`（`support_actions.py`）→ そのまま
- `InterventionBridge` 経由の HITL CONFIRM → そのまま

新規実装は **Segment / Detect / Severity の 3 つだけ**で、Retrieve・Ground・
誤検知抑止・Action は既存機構の再利用である。

## パイプライン（REVIEW_STEP_IDS の順に実行）

    S1 ruleset  RuleSet 適用（検索スコープ・しきい値・重大リスク語）
    ① segment   文書を検査単位へ分割（決定的・原文オフセット保持）
    ② retrieve  セグメントごとに規程を RAG 検索
    ③ detect    二段判定で違反候補を検出
    ④ ground    GroundednessVerifier で指摘の根拠を検証
    ④' suppress 誤検知抑止 + 救済
    ⑥ web       法改正の裏取り（任意・信頼度を下げる方向にのみ使う）
    ⑤ severity  重大度の確定（＋重大リスク語による強制 high）
    ⑦ action    レポート → HITL CONFIRM → バックエンド実行

番号は Support との対応を示す呼称であり、実行順とは一致しない
（Support で ④' が ⑤ の後に来るのと同じ）。
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.core.jobs import register_runner
from backend.app.core.review_gates import (
    adjust_severity,
    apply_forced_high,
    create_mention_classifier,
    create_vacuous_judge,
    create_violation_detector,
    decide_finding_status,
    detect_vacuous_finding,
    select_candidate_rules,
    select_document_rules,
    should_force_high,
    should_rescue_finding,
)
from backend.app.core.rulesets import (
    DEFAULT_CONFIRM_TH,
    DEFAULT_NOTIFY_TH,
    FindingStatus,
    RuleItem,
    RuleSet,
    Severity,
    get_ruleset,
)
from backend.app.core.support_agent import (
    AUTO_PROCEED,
    ConfirmFn,
    EmitFn,
    SupportEvent,
    _perform_action,
)
from backend.app.core.verticals import ActionRequest
from grace import create_intervention_handler, create_tool_registry, get_config
from grace.confidence import create_groundedness_verifier
from support_actions import create_action_backend

# UI のタイムライン表示と 1:1 対応するステップ ID。
REVIEW_STEP_IDS = (
    "ruleset",    # S1 RuleSet 適用
    "segment",    # ① 文書分割
    "retrieve",   # ② 規程検索
    "detect",     # ③ 違反候補の二段判定
    "ground",     # ④ 指摘の根拠検証
    "suppress",   # ④' 誤検知抑止 + 救済
    "web",        # ⑥ 法改正の裏取り
    "severity",   # ⑤ 重大度の確定
    "action",     # ⑦ レポート → HITL → 実行
)

# --- 組合せ爆発ガード（設計書 §7.3）------------------------------------------
# 200 セグメント × 21 ルールを無条件に第2段へ流すと 4,200 回の LLM 呼び出しになる。
# 第1段のキーワードフィルタで実際はこの 1〜2 割だが、上限は必ず置く。
MAX_SEGMENTS = 200
MAX_LLM_CALLS = 300
MAX_SEGMENT_CHARS = 400      # これを超える段落は文末で再分割する
RETRIEVE_LIMIT = 5           # ② の判定単位あたり取得件数

# 文書全体スコープの指摘に付ける segment_id。実セグメント（s001…）と衝突しない。
DOCUMENT_SEGMENT_ID = "doc"

# 文書全体スコープの「該当箇所」として採用する excerpt の上限（理由は `_is_too_broad`）。
DOCUMENT_EXCERPT_MAX_CHARS = 200
DOCUMENT_EXCERPT_MAX_RATIO = 0.4


# =============================================================================
# データモデル
# =============================================================================

@dataclass
class ReviewParams:
    """POST /api/review/submit のパラメータ。"""

    document: str
    document_title: str = "無題"
    ruleset: Optional[str] = "ec_ad"
    # Web 裏取りの既定は OFF。条文が一次情報であり、Web は速度・コストに見合わない。
    use_web: bool = False
    do_action: bool = True
    dry_run: bool = True
    verbose: bool = False


@dataclass
class Segment:
    """検査単位。`start` / `end` は**原文**の文字オフセット（UI のハイライト用）。"""

    segment_id: str
    text: str
    start: int
    end: int
    kind: str = "paragraph"      # "paragraph" | "list_item" | "heading"


@dataclass
class ReviewFinding:
    """1 件の指摘。UI の指摘カード 1 枚に対応する。"""

    finding_id: str
    segment_id: str
    excerpt: str
    start: int
    end: int

    rule_id: str
    rule_title: str
    category: str
    law: str
    article: str

    message: str
    suggestion: str

    severity: Severity = "medium"
    confidence: float = 0.0
    citations: List[str] = field(default_factory=list)

    status: FindingStatus = "review_required"
    forced: bool = False
    suppress_reason: Optional[str] = None
    web_checked: bool = False


@dataclass
class FindingSummary:
    high: int = 0
    medium: int = 0
    low: int = 0
    confirmed: int = 0
    review_required: int = 0
    suppressed: int = 0          # findings には含まれない（件数のみ）


@dataclass
class ReviewResult:
    """レビュー結果。`GET /api/review/result/{job_id}` と result イベントで返す。"""

    document_title: str
    ruleset: Optional[str] = None
    segments: List[Segment] = field(default_factory=list)
    findings: List[ReviewFinding] = field(default_factory=list)
    summary: FindingSummary = field(default_factory=FindingSummary)
    used_web: bool = False
    action: Optional[ActionRequest] = None
    action_result: Optional[str] = None
    # --- KPI 計測用メタデータ ---
    segments_total: int = 0
    rules_evaluated: int = 0     # 第2段 LLM を呼んだ (セグメント×ルール) 数
    detected_raw: int = 0        # 第2段が violates=True とした数
    rescued: int = 0             # ④-救済で残した数
    forced_high: int = 0         # 重大リスク語で強制 high にした数
    truncated: bool = False      # ガード上限に達して打ち切ったか


def review_result_to_dict(result: ReviewResult) -> Dict[str, Any]:
    """ReviewResult を JSON 化可能な dict にする。"""
    return asdict(result)


# =============================================================================
# ① Segment — 決定的な文書分割（LLM 不使用・原文オフセット保持）
# =============================================================================

# 箇条書き・見出しの行頭パターン。1 行 1 セグメントにする。
_LIST_RE = re.compile(r"^\s*(?:[・\-*＊]|[0-9０-９]+[.)．）])")
_HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s|[■◆●▼【])")
# 日本語・英語の文末
_SENTENCE_END_RE = re.compile(r"[。！？!?]")


def _trim_span(text: str, start: int, end: int) -> Tuple[int, int]:
    """前後の空白を除いたスパンを返す（オフセットは原文基準を維持）。"""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _split_long_span(text: str, start: int, end: int, max_chars: int) -> List[Tuple[int, int]]:
    """長いスパンを文末（。！？!?）で分割する。文末が無ければそのまま返す。"""
    if end - start <= max_chars:
        return [(start, end)]

    spans: List[Tuple[int, int]] = []
    cursor = start
    chunk_start = start
    for match in _SENTENCE_END_RE.finditer(text, start, end):
        cursor = match.end()
        if cursor - chunk_start >= max_chars:
            spans.append((chunk_start, cursor))
            chunk_start = cursor
    if chunk_start < end:
        spans.append((chunk_start, end))
    return spans or [(start, end)]


def split_segments(
    text: str,
    max_chars: int = MAX_SEGMENT_CHARS,
    max_segments: int = MAX_SEGMENTS,
) -> Tuple[List[Segment], bool]:
    """文書を検査単位へ分割する。

    分割規則（設計書 §3.3 ①）:
      - 空行で段落へ一次分割
      - 行頭が箇条書き・見出しなら 1 行 1 セグメント
      - `max_chars` を超える段落は文末で再分割
      - 空白のみのスパンは破棄

    ⚠️ **オフセットは必ず原文に対して取る。** 正規化を挟むと UI のハイライト位置が
    ずれるため、`text` へは一切手を加えない。

    Returns:
        (segments, truncated) — truncated は `max_segments` で打ち切ったか
    """
    segments: List[Segment] = []
    truncated = False

    def add(span_start: int, span_end: int, kind: str) -> bool:
        """1 セグメントを追加する。上限に達したら False を返す。"""
        nonlocal truncated
        span_start, span_end = _trim_span(text, span_start, span_end)
        if span_start >= span_end:
            return True
        for sub_start, sub_end in _split_long_span(text, span_start, span_end, max_chars):
            sub_start, sub_end = _trim_span(text, sub_start, sub_end)
            if sub_start >= sub_end:
                continue
            if len(segments) >= max_segments:
                truncated = True
                return False
            segments.append(Segment(
                segment_id=f"s{len(segments) + 1:03d}",
                text=text[sub_start:sub_end],
                start=sub_start,
                end=sub_end,
                kind=kind,
            ))
        return True

    # 空行（改行 + 空白のみの行 + 改行）で段落へ分割
    block_start = 0
    for blank in re.finditer(r"\n[ \t　]*\n", text):
        if not _emit_block(text, block_start, blank.start(), add):
            return segments, True
        block_start = blank.end()
    if not _emit_block(text, block_start, len(text), add):
        return segments, True

    return segments, truncated


def _emit_block(
    text: str,
    start: int,
    end: int,
    add: Callable[[int, int, str], bool],
) -> bool:
    """段落ブロックを、箇条書き・見出しかどうかで分けて追加する。"""
    start, end = _trim_span(text, start, end)
    if start >= end:
        return True

    # ブロック内に箇条書き・見出しが 1 行でもあれば、行単位に分割する
    lines: List[Tuple[int, int, str]] = []
    cursor = start
    has_marker = False
    while cursor < end:
        newline = text.find("\n", cursor, end)
        line_end = end if newline == -1 else newline
        line = text[cursor:line_end]
        if _HEADING_RE.match(line):
            kind = "heading"
            has_marker = True
        elif _LIST_RE.match(line):
            kind = "list_item"
            has_marker = True
        else:
            kind = "paragraph"
        lines.append((cursor, line_end, kind))
        cursor = line_end + 1

    if not has_marker:
        return add(start, end, "paragraph")
    for line_start, line_end, kind in lines:
        if not add(line_start, line_end, kind):
            return False
    return True


# =============================================================================
# ② Retrieve — 規程検索（rag_search を無改造で使用）
# =============================================================================

def _retrieve_evidence(
    tool_registry,
    query: str,
    ruleset: Optional[RuleSet],
    on_drop: Optional[Callable[[str], None]] = None,
    collections: Optional[List[str]] = None,
) -> Tuple[List[str], List[str]]:
    """判定単位に関連する規程を検索する。

    ⚠️ **関連度の低い規程は根拠として採用しない**（`RuleSet.evidence_min_score`）。

    実測 2026-08-17 20:07 では、条文コレクション `ec_ad_rules_anthropic` が未登録で
    `ec_policy_anthropic`（返品・返金・交換の FAQ）だけが存在したにもかかわらず、
    緩和閾値 0.5 で拾った 5 件がそのまま根拠になっていた。

        指摘: 販売価格・送料の明示（特定商取引法 第11条）
        根拠: [規程] 返品規定を教えてください, [規程] 不良品が届いた場合の対応…,
              [規程] 返金ポリシーを教えてください, [規程] 返品できない商品は…,
              [規程] 交換の条件を教えてください        ← 全部無関係

    しかも呼び出し側は `citations or [rule.citation()]` で分岐するため、**1 件でも
    拾えば正しい条文フォールバックが低スコアの FAQ に上書きされる**。
    「条文つきの指摘を出す」という機能の価値が崩れていた。

    スコアで絞れなかった規程は 0 件として返し、呼び出し側に
    `RuleItem.description`（条文フォールバック）を使わせる方が正確である。

    ⚠️ **絶対スコアに加えて、Top スコアとの相対比でも絞る**
    （`RuleSet.evidence_top_ratio`）。条文コレクションを登録すると中身は
    「互いに似た条文が並ぶ集合」になり、**どのルールで検索しても他ルールの条文が
    絶対閾値 0.70 を超えて付いてくる**（実測 2026-08-18 22:38: tokusho-01 の
    根拠 5 件中 4 件が別ルールの条文）。これは表示が汚れるだけでなく、
    ③ Detect の【規程】に他ルールの主題が混ざって**指摘文が越境する**という
    実害を出していた。詳細と実測値は `rulesets.DEFAULT_EVIDENCE_TOP_RATIO`。

    Args:
        on_drop: 関連度不足で落とした規程を伝える。⚠️ **`emit` 経由の実行ログ
            （UI・SSE）に出すために必要。** Python の logger だけに出すと、
            「なぜ根拠が条文フォールバックになったのか」を画面から追えない。
        collections: 検索対象コレクションの上書き（`RuleItem.evidence_collections`）。
            ⚠️ **ルール自身が根拠として引かれてしまうルールのための逃げ道。**
            `policy-01` が引きたいのは自社の実際の規程であって、条文コレクションに
            入っている policy-01 自身の行ではない。理由は `RuleItem` の宣言箇所。

    Returns:
        (citations, source_texts) — citations は UI 表示用ラベル、
        source_texts は ④ Ground の検証に渡す本文。
        閾値に届く規程が 1 件も無ければ両方とも空。
    """
    if ruleset is None:
        return [], []
    scope = list(collections or ruleset.collections)
    if not scope:
        return [], []
    try:
        res = tool_registry.execute(
            "rag_search",
            query=query,
            limit=RETRIEVE_LIMIT,
            allowed_collections=scope,
        )
    except Exception:
        return [], []
    if not res or not getattr(res, "success", False) or not res.output:
        return [], []

    min_score = ruleset.evidence_min_score
    # Top スコアとの相対比による足切り。絶対値の min_score では、互いに似た条文が
    # 並ぶコレクション（`ec_ad_rules_anthropic` はまさにそれ）で他ルールの条文が
    # 全部通ってしまう。理由と実測値は `rulesets.DEFAULT_EVIDENCE_TOP_RATIO`。
    scores = [
        float(e["score"])
        for e in res.output
        if isinstance(e, dict) and e.get("score") is not None
    ]
    cutoff = max(scores) * ruleset.evidence_top_ratio if scores else None

    citations: List[str] = []
    source_texts: List[str] = []
    dropped: List[str] = []
    far: List[str] = []
    for entry in res.output:
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload", {})
        title = payload.get("title") or payload.get("question") or "(規程)"
        # score が無い（＝スコアを持たない経路）ものは従来どおり通す。
        score = entry.get("score")
        if score is not None:
            score = float(score)
            if score < min_score:
                dropped.append(f"{title}({score:.4f})")
                continue
            if cutoff is not None and score < cutoff:
                far.append(f"{title}({score:.4f})")
                continue
        body = payload.get("answer") or payload.get("text") or ""
        label = f"[規程] {title}"
        if label not in citations:
            citations.append(label)
        if body:
            source_texts.append(body)

    if on_drop is not None:
        if dropped:
            tail = "→ 条文フォールバックを使います" if not citations else ""
            on_drop(f"  [retrieve] 関連度が低い規程を根拠にしません"
                    f"（< {min_score:.2f}）: {', '.join(dropped[:5])} {tail}".rstrip())
        if far:
            on_drop(f"  [retrieve] 最上位より離れた規程を根拠にしません"
                    f"（< {cutoff:.4f}）: {', '.join(far[:5])}")
    return citations, source_texts


# =============================================================================
# コアパイプライン
# =============================================================================

def run_review_agent_core(
    document: str,
    document_title: str = "無題",
    ruleset: Optional[str] = "ec_ad",
    use_web: bool = False,
    do_action: bool = True,
    dry_run: bool = True,
    verbose: bool = False,
    emit: Optional[EmitFn] = None,
    confirm: Optional[ConfirmFn] = None,
) -> Optional[ReviewResult]:
    """文書レビューのパイプラインを実行する。

    Args:
        emit: 進捗イベントのコールバック（None なら通知なし）
        confirm: HITL CONFIRM の解決コールバック。Web からは必ず
            `InterventionBridge.resolver` を渡すこと。
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

    # config.qdrant.allowed_collections / config.llm.prompt_addendum を RuleSet に
    # 合わせて書き換えるため、シングルトンをそのまま使うと jobs.py がジョブごとに
    # 立てるワーカースレッド同士で値を奪い合う（Review の検索スコープが並走中の
    # Support のスコープを上書きする等）。support_agent.py と同じく、リクエスト
    # 単位のディープコピーを作り、以降の生成物はすべてこのコピーを参照させる。
    config = copy.deepcopy(get_config())
    tool_registry = create_tool_registry(config)
    verifier = create_groundedness_verifier(config)
    detect = create_violation_detector(config)
    classify_mention = create_mention_classifier(config)
    vacuous_judge = create_vacuous_judge(config)
    # CLI（confirm=None）は自動承認。既定ドライランのため安全。
    # Web からは必ず InterventionBridge.resolver が渡る。
    resolve_confirm: ConfirmFn = confirm or (lambda _req: AUTO_PROCEED)
    handler = create_intervention_handler(
        config,
        on_notify=lambda msg: log(f"   [intervention/notify] {msg}", step="action"),
        on_confirm=resolve_confirm,
        on_escalate=resolve_confirm,
    )

    # --- S1 RuleSet 適用 ----------------------------------------------------
    rs = get_ruleset(ruleset)
    notify_th = rs.notify_th if rs else DEFAULT_NOTIFY_TH
    confirm_th = rs.confirm_th if rs else DEFAULT_CONFIRM_TH

    # コアへの配線: 検索スコープと業界方針を config へ注入する（Support と同じ手順）。
    config.qdrant.allowed_collections = list(rs.collections) if rs else []
    config.llm.prompt_addendum = rs.prompt_addendum if rs else ""

    if rs is not None:
        step_started("ruleset", f"S1 ルールセット: {rs.name}（--ruleset {ruleset}）",
                     ruleset=ruleset, name=rs.name)
        log(f"  検索スコープ: {', '.join(rs.collections) or '—'}"
            "（未登録コレクションは条文フォールバックを使用）", step="ruleset")
        log(f"  ルール数: {len(rs.rules)}（常時チェック {len(rs.always_check_rules)}）"
            f" / しきい値: notify={notify_th} confirm={confirm_th}", step="ruleset")
        step_finished("ruleset", ruleset=ruleset, name=rs.name,
                      rules=len(rs.rules), collections=list(rs.collections),
                      notify_th=notify_th, confirm_th=confirm_th)
    else:
        step_skipped("ruleset")

    # --- ① Segment ----------------------------------------------------------
    step_started("segment", "① Segment（文書を検査単位へ分割）")
    segments, seg_truncated = split_segments(document)
    if seg_truncated:
        log(f"  ⚠️ セグメント数が上限（{MAX_SEGMENTS}）に達したため以降を打ち切りました",
            step="segment")
    log(f"  {len(segments)} セグメントに分割（文字数 {len(document)}）", step="segment")
    step_finished("segment", segments=len(segments), truncated=seg_truncated,
                  chars=len(document))

    result = ReviewResult(
        document_title=document_title,
        ruleset=ruleset if rs else None,
        segments=segments,
        segments_total=len(segments),
        truncated=seg_truncated,
    )
    if not segments or rs is None:
        log("  検査対象が無いため終了します", step="segment")
        _emit(SupportEvent(type="result", data=review_result_to_dict(result)))
        return result

    # --- ②〜④' セグメントごとのループ ---------------------------------------
    step_started("retrieve", "② Retrieve（規程を RAG 検索）")
    step_started("detect", "③ Detect（二段判定で違反候補を検出）")
    step_started("ground", "④ Ground（指摘の根拠を検証）")
    step_started("suppress", "④' Suppress（誤検知抑止 + 救済）")

    findings: List[ReviewFinding] = []
    llm_calls = 0
    detected_raw = 0
    rescued = 0
    suppressed = 0
    truncated = seg_truncated

    def _evaluate(rule, target: Segment, citations, source_texts) -> None:
        """1 ルール × 1 判定単位を評価し、残すべきなら findings へ加える。

        文書全体パスとセグメントパスで ③〜④' の扱いを完全に同じにするため、
        両者で共有する。
        """
        nonlocal llm_calls, detected_raw, rescued, suppressed

        # 規程コレクションが未登録なら RuleItem.description を根拠に使う
        evidence_texts = source_texts or [rule.description]
        evidence = "\n\n".join(evidence_texts)
        rule_citations = citations or [rule.citation()]

        verdict = detect(target.text, rule, evidence)
        llm_calls += 1
        if verdict is not None and not verdict.violates:
            return

        detected_raw += 1
        finding = _build_finding(
            index=len(findings) + 1,
            segment=target,
            rule=rule,
            verdict=verdict,
            citations=rule_citations,
        )

        # ④ Ground — 指摘そのものが裏付けられるか
        #
        # ⚠️ **検査対象の本文も根拠として渡す。規程だけでは検証にならない。**
        #
        # 表記漏れルールの指摘文は「〜の記載がない」という**対象文書についての
        # 主張**である。条文は対象文書について何も述べていないので、条文だけを
        # 根拠にすると原理的に検証できない。実測 2026-08-19 05:35 がそれを示していた。
        #
        #   tokusho-03 判定内訳 —
        #     supported: 特定商取引法第11条は商品の引渡時期の記載を求めている
        #     supported: 注文からどの程度で商品が届くかが読み取れない広告は違反となる
        #     neutral  : 当該記述には商品の引渡時期の記載が見当たらない   ← 本題
        #     neutral  : 当該記述は特定商取引法第11条に抵触する           ← 本題
        #
        # 支持された 2 件は**条文が条文自身を支持している**だけのトートロジーで、
        # 本題の 2 件は条文からは判定不能なので neutral。それでも
        # `support_rate = 2/(2+0) = 1.00` となり confirmed になっていた。
        # **確信度 1.00 が何も測っていない。**
        #
        # 逆向きの誤りも同じ実行で出ていた。tokusho-01 では
        # 「送料が別途必要かどうかの記載がない」が supported と判定されたが、
        # 条文は当該広告について何も述べていない（条文が「送料の記載が無い場合」に
        # 言及しているだけ）。**偽の supported** である。
        #
        # 指摘文は 2 種類の主張が混ざっている。
        #   (i)  対象文書についての事実（「送料の記載がない」）→ 対象本文で検証できる
        #   (ii) 法的な結論（「第11条に抵触する」）            → 条文で検証できる
        # 両方を根拠として渡すことで、各主張が**正しい出典**に照合される。
        #
        # ⚠️ ③ Detect には従来どおり規程だけを渡す（対象テキストは別枠で渡している）。
        # ここで足すのは ④ Ground の検証材料だけ。
        ground_sources = evidence_texts + [
            f"【検査対象の本文（この文書に何が書かれているかの唯一の出典）】\n{target.text}"
        ]
        gres = verifier.verify(
            f"次の記述は「{rule.title}」（{rule.law} {rule.article}）に抵触するか",
            finding.message,
            ground_sources,
        )
        finding.confidence = gres.support_rate

        # ⚠️ **`gres.verified` だけでは「判定が得られた」ことにならない。**
        #
        # `verified = total > 0` は「LLM が主張を分解できたか」であって
        # 「支持／矛盾の判定が付いたか」ではない。全主張が neutral（＝規程で
        # 支持も否定もされていない）でも `verified=True` で通る。そのとき
        # `support_rate = supported / (supported + contradicted)` は分母 0 なので
        # 0.0 になるが、これは「1 件も支持されなかった」ではなく**測れていない**。
        #
        # 実測 2026-08-17 20:08:30（tokusho-01・全 5 主張が neutral）:
        #     判定内訳 — neutral / neutral / neutral / neutral / neutral
        #     → 確信度 0.00 で `suppressed` に落ち、救済で `review_required` へ
        #
        # 支持率 0.0 として扱うと `confirm_th`（0.60）を下回るので必ず
        # `suppressed` へ倒れ、救済（矛盾なし・根拠あり）に拾われて戻ってくる。
        # 救済が効かない条件（指摘文が空・根拠ゼロ）では**判定できていない指摘が
        # 黙って消える**。遠回りせず、最初から「未検証」として `review_required`
        # に倒す。
        #
        # Support 側は既にこの区別をしている（`grace/executor.py`:
        # `if not gres.verified or decided == 0:` で「判定不能（中立）」扱い、
        # `backend/app/core/gates.py` にも「全 neutral（decided=0）」の記述がある）。
        # **Review だけがこの `decided == 0` の判定を落としていた。**
        decided = gres.supported + gres.contradicted
        judged = gres.verified and decided > 0

        # ④' Suppress — status 判定と救済
        status = decide_finding_status(
            gres.support_rate, judged, len(finding.citations),
            notify_th, confirm_th,
        )
        if status == "suppressed" and should_rescue_finding(
            status, gres.has_contradiction, len(finding.citations),
            finding.message, vacuous_judge,
        ):
            status = "review_required"
            rescued += 1
            log(f"  [rescue] {rule.rule_id}: 矛盾なし・根拠ありのため保留として維持",
                step="suppress")
        finding.status = status

        if not judged and verbose:
            # ⚠️ 「支持率 0.00」と書かない（測れていないだけで、否定されたのではない）
            reason = (f"判定なし（{gres.total} 主張すべて neutral）" if gres.verified
                      else f"検証不能（{gres.reason or '理由不明'}）")
            log(f"  [ground] {rule.rule_id}: {reason} → 要確認として残します",
                step="ground")

        if status == "suppressed":
            vacuous, marker = detect_vacuous_finding(finding.message, vacuous_judge)
            finding.suppress_reason = (
                f"実質性なし（{marker}）" if vacuous else
                f"根拠不足（支持率 {gres.support_rate:.2f} / "
                f"{gres.supported}支持・{gres.contradicted}矛盾）"
            )
            suppressed += 1
            if verbose:
                log(f"  [suppress] {rule.rule_id}: {finding.suppress_reason}",
                    step="suppress")
            return

        findings.append(finding)
        log(f"  [{rule.rule_id}] {finding.message}", step="ground",
            finding=asdict(finding))

    # --- 判定単位 1: 文書全体（表記漏れ） -----------------------------------
    whole = _document_segment(document)
    for candidate in select_document_rules(rs):
        if llm_calls >= MAX_LLM_CALLS:
            truncated = True
            break
        rule = rs.rule_by_id(candidate.rule_id)
        if rule is None:
            continue
        # ⚠️ 検索クエリは**ルール自身**（文書全体ではない）。
        #    文書をそのままクエリにすると、長文では埋め込みが薄まって
        #    関連する規程を引けない。探したいのは「このルールの根拠条文」である。
        #    ただし policy-01 のように「引きたいのは条文ではなく自社の規程」という
        #    ルールは `RuleItem.evidence_query` / `evidence_collections` で上書きする。
        citations, source_texts = _retrieve_evidence(
            tool_registry, rule.retrieval_query(), rs,
            on_drop=lambda msg: log(msg, step="retrieve"),
            collections=rule.evidence_collections or None,
        )
        if verbose:
            log(f"  {DOCUMENT_SEGMENT_ID}/{rule.rule_id}: 文書全体で判定 / "
                f"規程 {len(citations)} 件", step="retrieve")
        _evaluate(rule, whole, citations, source_texts)

    # --- 判定単位 2: セグメント（キーワード型） -----------------------------
    for segment in segments:
        candidates = select_candidate_rules(segment.text, rs)
        if not candidates:
            continue

        citations, source_texts = _retrieve_evidence(
            tool_registry, segment.text, rs,
            on_drop=lambda msg: log(msg, step="retrieve"),
        )
        if verbose:
            log(f"  {segment.segment_id}: 候補 {len(candidates)} ルール / "
                f"規程 {len(citations)} 件", step="retrieve")

        for candidate in candidates:
            if llm_calls >= MAX_LLM_CALLS:
                truncated = True
                break
            rule = rs.rule_by_id(candidate.rule_id)
            if rule is None:
                continue
            _evaluate(rule, segment, citations, source_texts)

        if llm_calls >= MAX_LLM_CALLS:
            log(f"  ⚠️ LLM 呼び出しが上限（{MAX_LLM_CALLS}）に達したため打ち切りました",
                step="detect")
            break

    step_finished("retrieve", segments=len(segments))
    step_finished("detect", llm_calls=llm_calls, detected_raw=detected_raw,
                  truncated=truncated)
    step_finished("ground", verified=len(findings) + suppressed)
    step_finished("suppress", suppressed=suppressed, rescued=rescued,
                  kept=len(findings))

    # --- ⑥ Web 裏取り -------------------------------------------------------
    used_web = False
    if use_web and findings:
        step_started("web", "⑥ Web 裏取り（法改正・ガイドライン更新の確認）")
        used_web = _web_crosscheck(tool_registry, findings, rs, log)
        step_finished("web", checked=sum(1 for f in findings if f.web_checked))
    else:
        step_skipped("web", reason="無効" if not use_web else "指摘なし")

    # --- ⑤ Severity ---------------------------------------------------------
    step_started("severity", "⑤ Severity（重大度の確定＋重大リスク語の強制 high）")
    forced_high = 0
    for finding in findings:
        rule = rs.rule_by_id(finding.rule_id)
        base = rule.severity_default if rule else "medium"
        finding.severity = adjust_severity(
            base, finding.confidence, notify_th, confirm_th
        )
        target_text = finding.excerpt or _segment_text(segments, finding.segment_id)
        forced, keyword, mention = should_force_high(target_text, rs, classify_mention)
        finding.severity, finding.status = apply_forced_high(
            finding.severity, finding.status, forced
        )
        finding.forced = forced
        if forced:
            forced_high += 1
            log(f"  [forced] {finding.rule_id}: 重大リスク語 '{keyword}' を検知 → high",
                step="severity")
        elif keyword is not None:
            log(f"  [forced] 重大リスク語 '{keyword}' は {mention} → 強制せず",
                step="severity")
    step_finished("severity", forced_high=forced_high)

    result.findings = findings
    result.summary = _summarize(findings, suppressed)
    result.used_web = used_web
    result.rules_evaluated = llm_calls
    result.detected_raw = detected_raw
    result.rescued = rescued
    result.forced_high = forced_high
    result.truncated = truncated

    # --- ⑦ Action -----------------------------------------------------------
    action = _decide_review_action(result) if do_action else None
    if action is not None:
        backend = create_action_backend(dry_run=dry_run)
        step_started(
            "action",
            f"⑦ Action（レポート → intervention CONFIRM → ActionTool[{backend.name}]）",
            action_type=action.action_type,
            requires_confirmation=action.requires_confirmation,
            backend=backend.name,
            dry_run=dry_run,
        )
        log(f"  [action] 種別={action.action_type}（要承認={action.requires_confirmation}）",
            step="action")
        result.action = action
        # 文書レビューでは本人確認は不要（identity_verifier=None）
        result.action_result = _perform_action(
            action, handler, backend,
            identity_verifier=None, identity=None,
            emit_log=lambda msg: log(msg, step="action"),
        )
        log(f"  [action] {result.action_result}", step="action")
        step_finished("action", action_type=action.action_type, backend=backend.name,
                      dry_run=dry_run, result_message=result.action_result)
    else:
        step_skipped("action", reason="指摘なし" if do_action else "アクション無効")

    _emit(SupportEvent(type="result", data=review_result_to_dict(result)))
    return result


# =============================================================================
# 補助関数
# =============================================================================

def _document_segment(document: str) -> Segment:
    """文書全体を 1 つの判定単位として表す擬似セグメント。

    表記漏れ（`always_check`）の判定に使う。`result.segments` には入れない
    （UI のセグメント一覧は実際の分割結果だけを見せる）。
    """
    return Segment(
        segment_id=DOCUMENT_SEGMENT_ID,
        text=document,
        start=0,
        end=len(document),
        kind="document",
    )


def _is_too_broad(excerpt: str, document: str) -> bool:
    """文書全体スコープの `excerpt` が「該当箇所」として広すぎるか。

    表記漏れの指摘は特定の 1 箇所を指すためのものなので、文書の大半を占める
    excerpt は**位置を示せていない**（＝ポインタとして機能していない）。

    2 つの上限を **or** で判定する。片方だけでは取りこぼす:

    - 割合（`DOCUMENT_EXCERPT_MAX_RATIO`）… 短い文書向け。実測の 8 行の LP では
      7 行ぶん（約 0.87）が返ってきた。絶対値だけだと 140 文字は許容範囲に見える。
    - 絶対値（`DOCUMENT_EXCERPT_MAX_CHARS`）… 長い文書向け。5,000 文字の LP に対し
      1,000 文字の excerpt は割合では 0.2 だが、直す場所としては役に立たない。
    """
    if not document:
        return False
    return (
        len(excerpt) > DOCUMENT_EXCERPT_MAX_CHARS
        or len(excerpt) > len(document) * DOCUMENT_EXCERPT_MAX_RATIO
    )


def _build_finding(
    index: int,
    segment: Segment,
    rule: RuleItem,
    verdict,
    citations: List[str],
) -> ReviewFinding:
    """検出結果から ReviewFinding を組み立てる（原文オフセットを解決する）。

    `verdict is None`（LLM 判定失敗）の場合も指摘として残す。Review では
    指摘を消す方向のミスが最も痛いため、判定できないときは人に見せる。

    ⚠️ **文書全体スコープでは「該当箇所なし」を許す。** 表記漏れは
    「文書のどこにも書かれていない」ことの指摘なので、指し示せる箇所が
    そもそも存在しない。セグメントスコープと同じく「見つからなければ全体を
    ハイライト」にすると**文書全体が塗られる**ため、空スパン（start == end）を
    返して何もハイライトしない。フロントの `resolveOverlaps` は
    `end > start` で絞るので、空スパンは自然に無視される。
    """
    is_document = segment.kind == "document"
    excerpt = (verdict.excerpt if verdict else "") or ("" if is_document else segment.text)
    message = (verdict.message if verdict else "") or (
        f"「{rule.title}」に該当する可能性があります（自動判定に失敗したため要確認）"
    )
    suggestion = (verdict.suggestion if verdict else "") or "内容を確認してください"

    # ⚠️ 文書全体スコープでは**長すぎる excerpt を採用しない。**
    #
    # 表記漏れは「文書のどこにも書かれていない」ことの指摘なので、指し示せる箇所が
    # そもそも無い。それでも LLM は「該当箇所」を求められると、表記ブロックを丸ごと
    # 返してくることがある。それが文書内に見つかると位置解決に成功してしまい、
    # **文書のほとんどがハイライトされる**（実測 2026-08-17 23:50 / 08-18 21:41:
    # 8 行の LP に対し 7 行が該当箇所として表示された）。
    #
    # 「引渡時期が無い」という指摘に対して 7 行を塗っても、直す場所を示せていない。
    # 指し示せていないなら、いっそ何も塗らない方が正確である。
    if is_document and excerpt and _is_too_broad(excerpt, segment.text):
        excerpt = ""

    # excerpt が本文に含まれるなら、その位置を原文オフセットへ変換する。
    offset = segment.text.find(excerpt) if excerpt else -1
    if offset >= 0:
        start = segment.start + offset
        end = start + len(excerpt)
    elif is_document:
        # 指し示せる箇所が無い（＝表記が存在しない）。ハイライトしない。
        excerpt = ""
        start = end = 0
    else:
        # LLM が言い換えたためセグメント内に見つからない → セグメント全体を指す。
        excerpt = segment.text
        start, end = segment.start, segment.end

    return ReviewFinding(
        finding_id=f"f{index:03d}",
        segment_id=segment.segment_id,
        excerpt=excerpt,
        start=start,
        end=end,
        rule_id=rule.rule_id,
        rule_title=rule.title,
        category=rule.category,
        law=rule.law,
        article=rule.article,
        message=message,
        suggestion=suggestion,
        citations=list(citations),
    )


def _segment_text(segments: List[Segment], segment_id: str) -> str:
    for segment in segments:
        if segment.segment_id == segment_id:
            return segment.text
    return ""


def _web_crosscheck(tool_registry, findings, ruleset, log) -> bool:
    """`web_check=True` のルールについて法改正を確認する。

    ⚠️ **Web を根拠に新しい指摘は作らない**（出典の信頼性を担保できないため）。
    確認できたことを `web_checked` に記録するだけで、判定は変えない。
    """
    checked_rules: set = set()
    used = False
    for finding in findings:
        rule = ruleset.rule_by_id(finding.rule_id)
        if rule is None or not rule.web_check:
            continue
        if rule.rule_id not in checked_rules:
            checked_rules.add(rule.rule_id)
            try:
                res = tool_registry.execute(
                    "web_search", query=f"{rule.law} {rule.article} 改正 ガイドライン"
                )
            except Exception:
                res = None
            if res and getattr(res, "success", False) and res.output:
                used = True
                log(f"  [web] {rule.rule_id}: 最新ガイドラインを確認", step="web")
        finding.web_checked = True
    return used


def _summarize(findings: List[ReviewFinding], suppressed: int) -> FindingSummary:
    summary = FindingSummary(suppressed=suppressed)
    for finding in findings:
        setattr(summary, finding.severity, getattr(summary, finding.severity) + 1)
        if finding.status == "confirmed":
            summary.confirmed += 1
        elif finding.status == "review_required":
            summary.review_required += 1
    return summary


def _decide_review_action(result: ReviewResult) -> Optional[ActionRequest]:
    """指摘の内容から実行アクションを決める。

    - high の指摘があれば有人対応へ引き継ぐ（`escalate_to_human`）。
      引き継ぎそのものなので **承認不要**（承認待ちタイムアウトで宙に浮くのを防ぐ）。
    - confirmed の指摘のみなら起票（`create_ticket`・要承認）。
    - 指摘ゼロならアクションなし。
    """
    if not result.findings:
        return None
    args = {
        "document_title": result.document_title,
        "ruleset": result.ruleset,
        "findings": len(result.findings),
        "high": result.summary.high,
        "report": _build_report(result),
    }
    if result.summary.high > 0:
        return ActionRequest("escalate_to_human", args, requires_confirmation=False)
    return ActionRequest("create_ticket", args, requires_confirmation=True)


def _build_report(result: ReviewResult) -> str:
    """指摘レポート（Markdown）。アクションの引数と UI の書き出しに使う。"""
    lines = [
        f"# 表示チェック結果: {result.document_title}",
        "",
        f"- ルールセット: {result.ruleset or '（未適用）'}",
        f"- 指摘: {len(result.findings)} 件"
        f"（high {result.summary.high} / medium {result.summary.medium}"
        f" / low {result.summary.low}）",
        f"- 抑止: {result.summary.suppressed} 件",
        "",
    ]
    for finding in result.findings:
        lines += [
            f"## [{finding.severity.upper()}] {finding.rule_title}"
            f"（{finding.law} {finding.article}）",
            f"- 該当箇所: {finding.excerpt}",
            f"- 指摘: {finding.message}",
            f"- 修正案: {finding.suggestion}",
            f"- 根拠: {', '.join(finding.citations) or '—'}",
            f"- 確信度: {finding.confidence:.2f} / 状態: {finding.status}",
            "",
        ]
    return "\n".join(lines)


# =============================================================================
# ジョブ基盤への登録
# =============================================================================

def _review_runner(
    params: ReviewParams, emit: EmitFn, confirm: ConfirmFn
) -> Optional[Dict[str, Any]]:
    """`ReviewParams` → `run_review_agent_core` の呼び出し。"""
    result = run_review_agent_core(
        params.document,
        document_title=params.document_title,
        ruleset=params.ruleset,
        use_web=params.use_web,
        do_action=params.do_action,
        dry_run=params.dry_run,
        verbose=params.verbose,
        emit=emit,
        confirm=confirm,
    )
    return review_result_to_dict(result) if result is not None else None


# import 時に自己登録する。`ReviewParams` を構築するには本モジュールの import が
# 必要なため、登録漏れは構造的に起きない（設計書 §6.3）。
register_runner(ReviewParams, _review_runner, "review")
