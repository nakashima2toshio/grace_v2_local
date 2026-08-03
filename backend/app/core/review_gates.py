# backend/app/core/review_gates.py
"""GRACE-Review の判定ロジック（純関数群＋LLM 判定器ファクトリ）。

設計: backend/docs/review_agent_spec.md §3.3。

`gates.py`（Support の回答ゲート）と同じ構造を、「回答 → 指摘」へ読み替えた版。
判定の骨格は Support で実証済みのものをそのまま踏襲する。

| gates.py（Support） | 本モジュール（Review） |
|---|---|
| `_match_keyword` | `select_candidate_rules` が**そのまま再利用** |
| `_answer_gate` | `decide_finding_status` |
| `_should_force_escalate` | `should_force_high` |
| `_detect_no_info_answer` | `detect_vacuous_finding` |
| `_should_rescue_unaffirmed` | `should_rescue_finding` |
| `create_intent_classifier` | `create_mention_classifier` |
| `create_no_info_judge` | `create_vacuous_judge` |

## 過検知を抑える 3 機構（Support からの移植）

1. **二段判定**: 第1段でキーワード候補を絞り（LLM 呼び出しゼロ）、一致したものだけ
   第2段の LLM 判定へ回す。`always_check=True` のルールは第1段を素通しする。
2. **誤検知抑止**: 根拠の弱い指摘・実質性のない指摘を `suppressed` として落とす。
3. **救済**: 「矛盾はしていないが支持が弱い」だけの指摘は棄却せず `review_required`
   に落とす。消すのではなく人に見せることで見落とし（false negative）を防ぐ。

## LLM 判定が失敗したときの方針

分類・判定に失敗した（None が返る）場合は**安全側に倒す**。Review における安全側は
「指摘を消さない = `review_required` にする」であり、Support の「回答せず escalate」と
同じ考え方（誤って人に届けないより、人に確認してもらう方が損失が小さい）。
"""
from __future__ import annotations

import sys
from typing import Callable, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from backend.app.core.gates import _match_keyword
from backend.app.core.rulesets import FindingStatus, RuleItem, RuleSet, Severity
from backend.app.core.verticals import INTENT_MODEL
from config import ModelConfig

# ③ Detect の第2段に使うモデル。指摘文・修正案の生成を伴うため軽量モデルではなく既定モデル。
DETECT_MODEL = ModelConfig.DEFAULT_MODEL

# 重大リスク語がどう使われているかの分類（強制 high の第2段）。
#   claim     : その表現で実際に訴求している → 強制 high
#   negation  : 「使用しません」等の否定・方針表明 → 抑止（誤検知）
#   quotation : 条文の引用・用語の説明 → 抑止（誤検知）
Mention = Literal["claim", "negation", "quotation"]

# 重大度の並び（adjust_severity の 1 段下げに使う）
_SEVERITY_ORDER: Tuple[Severity, ...] = ("low", "medium", "high")


# 「実質的な指摘になっていない」候補句（誤検知抑止の第1段）。
# LLM が「特に問題ありません」の類を message に書いてしまうケースを拾う。
# 候補検出であり、最終判定は第2段の LLM が行う（本文中に説明として現れる場合があるため）。
VACUOUS_MARKERS = (
    "問題ありません",
    "問題はありません",
    "問題なし",
    "該当しません",
    "抵触しません",
    "違反しません",
    "指摘事項はありません",
    "特に問題",
)


class DetectVerdict(BaseModel):
    """③ Detect 第2段の LLM 応答スキーマ。"""

    violates: bool = Field(False, description="そのルールに抵触するか")
    message: str = Field("", description="指摘内容（1〜2文）")
    suggestion: str = Field("", description="修正案（1文）")
    excerpt: str = Field("", description="該当箇所（対象テキストの部分文字列）")


class RuleCandidate(BaseModel):
    """第1段を通過した (セグメント × ルール) の候補。"""

    rule_id: str
    matched_keyword: Optional[str] = None   # always_check の場合は None
    always_check: bool = False

    model_config = {"frozen": True}


# =============================================================================
# ① 第1段: キーワード候補検出（LLM 呼び出しなし）
# =============================================================================

def select_candidate_rules(
    segment_text: str,
    ruleset: Optional[RuleSet],
) -> List[RuleCandidate]:
    """セグメントに対して第2段へ回すルール候補を選ぶ。

    - `always_check=True` のルール（特商法の表記漏れ等）は常に候補にする。
      表記が「無い」ことの検出はキーワード一致では原理的に不可能なため。
    - それ以外は `RuleItem.keywords` の部分一致で絞る（`gates._match_keyword` を再利用）。

    Returns:
        候補のリスト。空なら第2段の LLM 呼び出しは 1 回も発生しない。
    """
    if ruleset is None:
        return []

    candidates: List[RuleCandidate] = []
    for rule in ruleset.always_check_rules:
        candidates.append(RuleCandidate(rule_id=rule.rule_id, always_check=True))

    if not segment_text:
        return candidates

    for rule in ruleset.keyword_rules:
        matched = _match_keyword(segment_text, rule.keywords)
        if matched is not None:
            candidates.append(
                RuleCandidate(rule_id=rule.rule_id, matched_keyword=matched)
            )
    return candidates


# =============================================================================
# ② 第2段: 違反検出（LLM）
# =============================================================================

def create_violation_detector(
    config,
) -> Callable[[str, RuleItem, str], Optional[DetectVerdict]]:
    """(セグメント本文, ルール, 規程根拠) → 抵触判定 の LLM 検出器を返す。

    判定できない場合（API エラー・スキーマ不一致）は None を返し、呼び出し側が
    安全側（指摘として残し `review_required`）に倒す。
    """
    from grace.llm_compat import create_chat_client

    client = create_chat_client(config)
    addendum = getattr(getattr(config, "llm", None), "prompt_addendum", "") or ""

    def detect(text: str, rule: RuleItem, evidence: str) -> Optional[DetectVerdict]:
        prompt = (
            "あなたは広告表示のコンプライアンス担当です。"
            "次の【対象テキスト】が【ルール】に抵触するかを判定してください。\n\n"
            "判定の原則:\n"
            "- 【規程】に書かれている内容のみを根拠にすること。推測で指摘しないこと。\n"
            "- 抵触しない場合は violates=false とし、message は空にすること。\n"
            "- 抵触する場合、excerpt には対象テキストから該当箇所をそのまま抜き出すこと"
            "（言い換えない）。\n"
            "- message は何がどう問題かを 1〜2 文で述べること。\n"
            "- suggestion は具体的な修正案を 1 文で述べること。\n"
            "- 否定文脈（「〜という表現は使用しません」）や、ルールの説明・引用は"
            "抵触ではない。violates=false とすること。\n"
            f"{addendum}\n\n"
            f"# ルール\n{rule.title}（{rule.law} {rule.article}）\n\n"
            f"# 規程\n{evidence}\n\n"
            f"# 対象テキスト\n{text}\n"
        )
        try:
            response = client.models.generate_content(
                model=DETECT_MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": DetectVerdict,
                    "temperature": 0.0,
                    "max_output_tokens": 512,
                },
            )
            if not response or not response.text:
                print(f"   [detect] 空応答（{rule.rule_id}）→ 安全側（要確認）",
                      file=sys.stderr)
                return None
            return DetectVerdict.model_validate_json(response.text)
        except Exception as e:
            print(f"   [detect] 判定に失敗（{rule.rule_id} / {type(e).__name__}）"
                  "→ 安全側（要確認）", file=sys.stderr)
            return None

    return detect


# =============================================================================
# ③ 重大リスク語の二段判定（強制 high）
# =============================================================================

def create_mention_classifier(config) -> Callable[[str], Optional[Mention]]:
    """重大リスク語の使われ方を分類する軽量 LLM 判定器を返す。

    キーワード候補が一致したときだけ呼ばれるため、追加コストは軽量モデル 1 回に留まる。
    分類できない場合は None を返し、呼び出し側が安全側（強制 high）へ倒す。
    """
    from grace.llm_compat import create_chat_client

    client = create_chat_client(config)

    def classify(text: str) -> Optional[Mention]:
        prompt = (
            "次の文が、強調表現をどのように扱っているかを 1 語で分類してください。\n\n"
            "- claim     : その表現を使って実際に商品・サービスを訴求している\n"
            "  （例:「業界No.1の品質です」「最安値でご提供」）\n"
            "- negation  : その表現を使わない・禁止する等の否定や方針表明\n"
            "  （例:「No.1という表現は使用しません」「最安値表示は行いません」）\n"
            "- quotation : 条文・ガイドラインの引用や、用語そのものの説明\n"
            "  （例:「No.1表示には客観的な調査が必要とされている」）\n\n"
            f"文: {text}\n\n"
            "出力（claim / negation / quotation のいずれか 1 語のみ）:"
        )
        try:
            response = client.models.generate_content(
                model=INTENT_MODEL,
                contents=prompt,
                config={"temperature": 0.0, "max_output_tokens": 10},
            )
            output = (response.text or "").strip().lower()
            for label in ("negation", "quotation", "claim"):
                if label in output:
                    return label
            print(f"   [mention] 想定外の分類出力: {output!r} → 安全側（強制 high）",
                  file=sys.stderr)
        except Exception as e:
            print(f"   [mention] 分類に失敗（{type(e).__name__}）→ 安全側（強制 high）",
                  file=sys.stderr)
        return None

    return classify


def should_force_high(
    text: str,
    ruleset: Optional[RuleSet],
    classify: Optional[Callable[[str], Optional[Mention]]] = None,
) -> Tuple[bool, Optional[str], Optional[Mention]]:
    """重大リスク語による強制 high の二段判定。

    第1段: `critical_keywords` の部分一致（候補検出）。
    第2段: 使われ方の分類。`negation` / `quotation` なら誤検知として強制しない。
    分類器が無い・分類失敗（None）の場合は安全側＝強制する。

    Returns:
        (forced, matched_keyword, mention)
    """
    if ruleset is None:
        return False, None, None
    matched = _match_keyword(text, ruleset.critical_keywords)
    if matched is None:
        return False, None, None
    mention = classify(text) if classify is not None else None
    if mention in ("negation", "quotation"):
        return False, matched, mention
    return True, matched, mention


# =============================================================================
# ④ 誤検知抑止（実質性のない指摘を落とす）
# =============================================================================

def create_vacuous_judge(config) -> Callable[[str], Optional[bool]]:
    """指摘文が実質的かを判定する軽量 LLM 判定器を返す。

    True = 実質性なし（vacuous）、False = 実質的な指摘、None = 判定不能。
    候補句が一致したときだけ呼ばれる。
    """
    from grace.llm_compat import create_chat_client

    client = create_chat_client(config)

    def judge(message: str) -> Optional[bool]:
        prompt = (
            "次の文が、広告表示の指摘として実質的な内容を持つかを判定してください。\n\n"
            "- substantive : 何がどう問題かを具体的に述べている\n"
            "  （「〜という表示は根拠の明示が無く優良誤認のおそれがある」等）\n"
            "- vacuous     : 問題が無い旨の報告、または内容の無い定型文に留まる\n"
            "  （「特に問題ありません」「該当しません」等）\n\n"
            f"文: {message}\n\n"
            "出力（substantive / vacuous のいずれか 1 語のみ）:"
        )
        try:
            response = client.models.generate_content(
                model=INTENT_MODEL,
                contents=prompt,
                config={"temperature": 0.0, "max_output_tokens": 10},
            )
            output = (response.text or "").strip().lower()
            if "vacuous" in output:
                return True
            if "substantive" in output:
                return False
            print(f"   [vacuous] 想定外の判定出力: {output!r} → 安全側（残す）",
                  file=sys.stderr)
        except Exception as e:
            print(f"   [vacuous] 判定に失敗（{type(e).__name__}）→ 安全側（残す）",
                  file=sys.stderr)
        return None

    return judge


def detect_vacuous_finding(
    message: str,
    judge: Optional[Callable[[str], Optional[bool]]] = None,
) -> Tuple[bool, Optional[str]]:
    """指摘文の実質性の二段判定。

    第1段: `VACUOUS_MARKERS` の部分一致（候補検出）。不一致なら LLM は呼ばず False。
    第2段: LLM 判定。判定失敗（None）は**安全側＝残す**（False）。

    Support の `_detect_no_info_answer` は判定失敗を True（escalate）に倒すが、
    こちらは False（指摘を残す）が安全側になる。落とす方向の判断だけを LLM に
    委ねる形にし、判定できないときに指摘が消えることを防ぐ。

    Returns:
        (vacuous, matched_marker)
    """
    marker = _match_keyword(message or "", VACUOUS_MARKERS)
    if marker is None:
        return False, None
    if judge is None:
        return False, marker
    verdict = judge(message)
    if verdict is True:
        return True, marker
    return False, marker


# =============================================================================
# ⑤ 指摘ゲート（status 判定）と救済
# =============================================================================

def decide_finding_status(
    support_rate: float,
    verified: bool,
    citation_count: int,
    notify_th: float,
    confirm_th: float,
) -> FindingStatus:
    """根拠の支持率から指摘の確定状態を決める純関数。

    `gates._answer_gate` と同型。対応:
      - ("answer", False)  → "confirmed"       高信頼。自動で指摘確定
      - ("answer", True)   → "review_required" 中信頼。人間の確認が必要
      - ("escalate", False)→ "suppressed"      低信頼。誤検知として除外（救済の対象）

    未検証（`verified=False`）・根拠ゼロは、Support では escalate に倒すが、
    Review では**指摘を消さない**方針のため `review_required` にする。
    """
    if not verified or citation_count == 0:
        return "review_required"
    if support_rate >= notify_th:
        return "confirmed"
    if support_rate >= confirm_th:
        return "review_required"
    return "suppressed"


def should_rescue_finding(
    status: FindingStatus,
    has_contradiction: bool,
    citation_count: int,
    message: str,
    judge: Optional[Callable[[str], Optional[bool]]] = None,
) -> bool:
    """`suppressed` に落ちた指摘を `review_required` へ救済するか。

    `gates._should_rescue_unaffirmed` と同型。根拠検証器の出力ぶれで支持率が下がった
    だけの指摘を、矛盾の有無で救う。以下をすべて満たすときだけ救済する:

      - `status` が `suppressed`（それ以外は救済不要）
      - 規程と矛盾していない（矛盾ありは誤指摘の可能性が高いので落とす）
      - 根拠条文が 1 件以上あり、指摘文が空でない
      - 指摘文が実質的である（「問題ありません」型は救済しない）
    """
    if status != "suppressed":
        return False
    if has_contradiction or citation_count == 0 or not message:
        return False
    return not detect_vacuous_finding(message, judge)[0]


# =============================================================================
# ⑥ 重大度の調整
# =============================================================================

def adjust_severity(
    base: Severity,
    support_rate: float,
    notify_th: float,
    confirm_th: float,
) -> Severity:
    """ルール既定の重大度を、根拠の強さで調整する純関数。

    支持率が中程度（confirm_th 以上 notify_th 未満）の指摘は 1 段下げる。
    根拠が弱い指摘を high のまま出すと、指摘リストの優先度が信用されなくなるため。
    confirm_th 未満は ④' で `suppressed` / 救済の対象になるので、ここでは下げない。
    """
    if support_rate >= notify_th or support_rate < confirm_th:
        return base
    index = _SEVERITY_ORDER.index(base)
    return _SEVERITY_ORDER[max(0, index - 1)]


def apply_forced_high(
    severity: Severity,
    status: FindingStatus,
    forced: bool,
) -> Tuple[Severity, FindingStatus]:
    """重大リスク語による強制 high を適用する純関数。

    強制時は `high` かつ `review_required` に引き上げる。`confirmed`（自動確定）にも
    しないのは、重大リスク語は「必ず人が見る」ための仕組みだから。
    """
    if not forced:
        return severity, status
    return "high", "review_required"
