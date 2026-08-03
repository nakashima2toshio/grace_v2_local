# backend/app/core/gates.py
"""回答ゲート・強制エスカレ・情報なし検知・救済などの純ロジック関数群。

`agent_support_example.py` から移設（React マイグレーション）。判定結果が
CLI 版と同一になるよう、ロジックは一切変更していない。後方互換のため
`agent_support_example` が再エクスポートする。
"""
from __future__ import annotations

import sys
from typing import Callable, List, Optional

from backend.app.core.verticals import (
    INTENT_MODEL,
    ActionRequest,
    Decision,
    Intent,
    VerticalProfile,
)


def create_intent_classifier(config) -> Callable[[str], Optional[Intent]]:
    """問い合わせ意図の LLM 分類器（軽量モデル・二段判定の第 2 段）を返す。

    返す関数は query を question / request / incident のいずれかへ分類する。
    分類できない場合（API エラー・想定外の出力）は None を返し、呼び出し側が
    安全側（従来のキーワード判定どおり）に倒す。呼び出しはキーワード候補が
    一致したときだけなので、追加コストは軽量モデル 1 呼び出しに限られる。
    """
    from grace.llm_compat import create_chat_client

    client = create_chat_client(config)

    def classify(query: str) -> Optional[Intent]:
        prompt = (
            "あなたはカスタマーサポートの一次受付です。次の問い合わせの意図を 1 語で分類してください。\n\n"
            "- question : 情報・手順・制度・規定を知りたい（FAQ質問。例:「課金プランの違いを教えて」「解約方法を教えて」）\n"
            "- request  : 操作・手続きの実行を依頼したい（例:「返品したい」「解約したい」「申請様式がほしい」）\n"
            "- incident : 障害・被害・トラブルの発生報告（例:「サービスが落ちています」「二重に課金された」「商品が破損していた」）\n\n"
            f"問い合わせ: {query}\n\n"
            "出力（question / request / incident のいずれか 1 語のみ）:"
        )
        try:
            response = client.models.generate_content(
                model=INTENT_MODEL,
                contents=prompt,
                config={"temperature": 0.0, "max_output_tokens": 10},
            )
            text = (response.text or "").strip().lower()
            for label in ("incident", "request", "question"):
                if label in text:
                    return label
            print(f"   [intent] 想定外の分類出力: {text!r} → キーワード判定を優先", file=sys.stderr)
        except Exception as e:
            print(f"   [intent] 意図分類に失敗（{type(e).__name__}）→ キーワード判定を優先", file=sys.stderr)
        return None

    return classify


def _match_keyword(query: str, keywords) -> Optional[str]:
    """キーワード候補の部分一致（二段判定の第 1 段）。最初に一致した語を返す。"""
    for keyword in keywords:
        if keyword in query:
            return keyword
    return None


# 「情報なし回答」の候補検出パターン（第 1 段）。誠実な回答ほど
# 「〜は見当たりませんでした」と明言するため、回答ゲート（支持率・出典数）を
# answer で通過してしまう。定型句はあくまで候補検出であり、最終判定は
# 第 2 段の LLM（実質回答か否か）が行う（実質回答の補足として同じ句が
# 現れるケースがあるため。例: 返品規定の回答末尾の「弊社固有の規定は
# 見当たりませんでした」）。活用差を吸収するため語幹で照合する。
NO_INFO_MARKERS = (
    "見当たりません",
    "見つかりません",
    "確認できません",
    "確認ができません",
    "情報がありません",
    "情報はありません",
)


def create_no_info_judge(config) -> Callable[[str, str], Optional[bool]]:
    """「情報なし回答」の LLM 判定器（軽量モデル・二段判定の第 2 段）を返す。

    返す関数は (query, answer) を受け、回答が質問の中心的な事柄に実質的に
    答えていれば False（answered）、「見つからない・お問い合わせください」に
    留まるなら True（no_info）を返す。判定できない場合（API エラー・想定外の
    出力）は None を返し、呼び出し側が安全側（escalate）に倒す。呼び出しは
    NO_INFO_MARKERS が一致したとき、または出典が Web のみ（社内根拠ゼロ）の
    回答に限られるので、追加コストは軽量モデル 1 呼び出しに留まる。
    """
    from grace.llm_compat import create_chat_client

    client = create_chat_client(config)

    def judge(query: str, answer: str) -> Optional[bool]:
        prompt = (
            "あなたはカスタマーサポートの品質チェック担当です。"
            "次の回答が、質問されたトピックに実質的に答えているかを判定してください。\n\n"
            "- answered : 質問されたトピックについて実質的な内容（規定・手順・条件・料金の目安・\n"
            "  一般的なルールなど）を 1 つでも提供している。一般論・参考情報ベースの回答でもよい。\n"
            "  「弊社固有の情報は見当たらなかった」という断り書きがあっても、本体が内容を\n"
            "  提供していれば answered。制度や仕組みの説明を求める一般知識の質問に、公的情報を\n"
            "  根拠として定義・特徴を説明する回答も answered。\n"
            "- no_info  : 質問された事柄そのもの（日付・金額・可否・内容）について実質的な情報が\n"
            "  ゼロで、「見つからない・確認できない」という報告と、確認方法の案内・他窓口への\n"
            "  誘導・他社や一般サイトの事例紹介だけで構成されている。\n"
            "  「質問された事柄そのもの」と「それをどこで確認できるかの案内」は区別すること。\n"
            "  後者だけの回答は、案内が丁寧でも no_info。\n"
            "  また、質問が将来の予測・見通しを求めており、回答が確定情報ではなく要望・検討段階の\n"
            "  情報の紹介に留まる（「確定した内容ではない」等の注記つき）場合も no_info\n"
            "  （不確実な予測は有人対応に回すべきため）。\n\n"
            "判定例:\n"
            "- 質問「返品規定を教えて」に、一般的な返品ルール（30日以内・法定8日等）を提示し、\n"
            "  末尾で「弊社固有の規定は見当たりませんでした」と断る回答 → answered\n"
            "- 質問「送料はいくら？」に、一般的な料金の目安表を提示する回答 → answered\n"
            "- 質問「〜とはどんな制度ですか？」に、公的サイトを根拠として制度の目的・対象・\n"
            "  手続きを説明する回答 → answered\n"
            "- 質問「この商品の入荷予定日は？」に、日付を一切示せず、「商品ページで確認できる\n"
            "  場合がある」等の一般的な確認方法の案内と問い合わせ先への誘導のみの回答 → no_info\n"
            "- 質問「来年の〜の予測は？」に、確定情報ではない要望・検討段階の情報を紹介し、\n"
            "  「正式に確定した内容ではない」と注記する回答 → no_info\n\n"
            f"質問: {query}\n\n回答:\n{answer}\n\n"
            "出力（answered / no_info のいずれか 1 語のみ）:"
        )
        try:
            response = client.models.generate_content(
                model=INTENT_MODEL,
                contents=prompt,
                config={"temperature": 0.0, "max_output_tokens": 10},
            )
            text = (response.text or "").strip().lower()
            if "no_info" in text or "no-info" in text:
                return True
            if "answered" in text:
                return False
            print(f"   [no-info] 想定外の判定出力: {text!r} → 安全側（escalate）", file=sys.stderr)
        except Exception as e:
            print(f"   [no-info] 実質回答判定に失敗（{type(e).__name__}）→ 安全側（escalate）", file=sys.stderr)
        return None

    return judge


def _detect_no_info_answer(
    query: str,
    answer: str,
    judge: Optional[Callable[[str, str], Optional[bool]]] = None,
    force_judge: bool = False,
) -> tuple[bool, Optional[str]]:
    """「情報なし回答」の二段判定（docs/vertical_spec_review.md の残課題①）。

    第 1 段: NO_INFO_MARKERS の部分一致（候補検出）。不一致なら LLM は呼ばず False。
    第 2 段: LLM 判定。実質回答（answered）なら False、no_info なら True。
    判定器が無い場合は従来どおり回答を通す（False）。判定失敗（None）は
    誤答を届けるより有人へ回す方が安全なので True（escalate）に倒す。

    force_judge=True（出典が Web のみ＝社内根拠ゼロの回答）の場合は、候補句が
    一致しなくても第 2 段の LLM 判定を必ず実施する。社内根拠ゼロの回答は
    「確認方法の案内だけ」「非確定の予測情報の紹介だけ」でも候補句を含まない
    ことがあり、answer で通過してしまうため（out-of-scope × 動的 Web 検索）。

    Returns:
        (no_info, matched_marker)
    """
    marker = _match_keyword(answer or "", NO_INFO_MARKERS)
    if marker is None and not (force_judge and answer):
        return False, None
    if judge is None:
        return False, marker
    verdict = judge(query, answer)
    if verdict is False:
        return False, marker
    return True, marker


def _should_force_escalate(
    query: str,
    profile: Optional[VerticalProfile],
    classify: Optional[Callable[[str], Optional[Intent]]] = None,
) -> tuple[bool, Optional[str], Optional[Intent]]:
    """強制エスカレの二段判定。

    第 1 段: `escalate_keywords` の部分一致（候補検出）。
    第 2 段: 意図分類。intent が "question"（FAQ質問）なら誤検知とみなして
    強制エスカレしない（例: SaaS「課金プランの違いを教えて」）。request /
    incident はエスカレ話題への依頼・報告なので設計どおり有人へ倒す
    （例: gov「減免を個別に判断してほしい」）。分類器が無い・分類失敗（None）
    の場合は安全側＝従来どおり強制エスカレする。

    Returns:
        (forced, matched_keyword, intent)
    """
    if profile is None:
        return False, None, None
    matched = _match_keyword(query, profile.escalate_keywords)
    if matched is None:
        return False, None, None
    intent = classify(query) if classify is not None else None
    if intent == "question":
        return False, matched, intent
    return True, matched, intent


def _answer_gate(
    support_rate: float,
    verified: bool,
    citation_count: int,
    notify_th: float,
    confirm_th: float,
) -> tuple[Decision, bool]:
    """支持率・出典数から回答可否を判定する純関数。

    Returns:
        (decision, warning):
          - ("answer", False): 高信頼（支持率>=notify かつ 出典>=1）
          - ("answer", True) : 中信頼（confirm<=支持率<notify）→ 未確認の注意
          - ("escalate", False): 低信頼／未検証／出典0 → 有人へ
    """
    if not verified or citation_count == 0:
        return "escalate", False
    if support_rate >= notify_th:
        return "answer", False
    if support_rate >= confirm_th:
        return "answer", True
    return "escalate", False


def _pick_groundedness(*results) -> tuple[float, int]:
    """複数の GroundednessResult から (支持率, 判定できた主張数) を選ぶ純関数。

    支持率が最大の検証結果を採用し、その decided（supported+contradicted）を
    併せて返す。同率の場合は decided が多い方（判定の裏付けが強い方）を選ぶ。
    KPI 側で「支持率が低い」と「判定不能（decided=0）」を区別するために使う。
    """
    return max(
        (g.support_rate, g.supported + g.contradicted) for g in results
    )


def _should_rescue_unaffirmed(
    decision: Decision,
    forced_escalate: bool,
    has_contradiction: bool,
    citation_count: int,
    answer: str,
    query: str,
    no_info_judge: Optional[Callable[[str, str], Optional[bool]]] = None,
) -> bool:
    """出典付き・非「情報なし」・矛盾なしの内部回答を escalate から救うか。

    `_answer_gate` の支持率は supported/decided で算出されるため、根拠検証器
    （Haiku）の出力ぶれで、出典付きの良質な内部RAG回答でも escalate に倒れる:
      - 全 neutral（decided=0）や JSON 崩れ（verified=False）→ 支持率 0.0
      - 一部だけ肯定（例 supported=1 / contradicted=2 → 0.33 < confirm_th）
    いずれも「肯定の裏付けが弱い」だけで、**矛盾は検出されていない**。放置すると
    ⑤ の Web 二次生成へ流れ、無関係な一般Web結果から「情報なし」回答に化けて
    誤エスカレする（ec「返金ポリシー」「送料」/ saas「レート制限」で顕在化）。

    そこで支持数の多寡ではなく「矛盾がないか」で判定する。以下をすべて満たす
    ときだけ救済（answer 継続。未確認注記付き）を許可する:
      - gate 判定が escalate かつ 強制エスカレでない（エスカレ語は最優先で維持）
      - 矛盾が検出されていない（矛盾ありは安全側に倒し従来どおり escalate）
      - 出典が 1 件以上あり、回答本文が空でない
      - その回答が実質回答である（範囲外の「情報なし」回答は除外＝従来どおり
        escalate。例: saas「来期の売上見込み」/ ec「入荷予定日」）
    """
    if decision != "escalate" or forced_escalate:
        return False
    if has_contradiction or citation_count == 0 or not answer:
        return False
    return not _detect_no_info_answer(query, answer, no_info_judge)[0]


def _decide_action(
    query: str,
    decision: Decision,
    profile: Optional[VerticalProfile] = None,
    classify: Optional[Callable[[str], Optional[Intent]]] = None,
) -> Optional[ActionRequest]:
    """問い合わせ内容と回答判定から、必要なアクションを決める（二段判定）。

    第 1 段: キーワード一致で候補を検出（プロファイル指定時は `action_map`、
    未指定時はデモ用の既定マッピング）。第 2 段: 意図分類。intent が
    "question"（FAQ質問。例:「解約方法を教えて」）ならアクションは起票せず
    回答のみとする。分類器が無い・分類失敗（None）の場合は従来どおり起票する
    （副作用は後段の CONFIRM でも守られる）。escalate 時は常に有人エスカレ。
    """
    if decision == "escalate":
        # escalate_to_human は「有人対応への引き継ぎ」そのもの（安全側の終端アクション）。
        # 承認（CONFIRM）を課すと、タイムアウト時に引き継ぎ自体が実行されず宙に浮く
        # ため、承認不要（requires_confirmation=False）とし、直接実行する。
        return ActionRequest("escalate_to_human", {"query": query}, requires_confirmation=False)

    request: Optional[ActionRequest] = None
    if profile is not None:
        matched = _match_keyword(query, profile.action_map)
        if matched is not None:
            request = ActionRequest(
                profile.action_map[matched], {"query": query, "matched": matched}
            )
    # 既定（プロファイル無し）
    elif _match_keyword(query, ("解約", "キャンセル", "退会")):
        request = ActionRequest("create_ticket", {"subject": "解約希望", "query": query})
    elif _match_keyword(query, ("パスワード", "ログイン", "サインイン")):
        request = ActionRequest("send_reply", {"template": "password_reset", "query": query})

    if request is None:
        return None
    if classify is not None and classify(query) == "question":
        return None  # FAQ 質問 → 回答のみ（起票・返信テンプレは不要）
    return request


def _collect_citations(step_results) -> List[str]:
    """各ステップの sources を重複排除して出典リストにする。

    executor は RAG スコア不足時に web_search を**動的挿入**するため、
    step_results には Web 由来の出典（URL）が混ざる。URL は [Web]、
    それ以外（社内ナレッジのファイル名等）は [社内] とラベル付けする。
    """
    seen: List[str] = []
    for sr in step_results:
        for src in sr.sources:
            if not src:
                continue
            prefix = "[Web]" if str(src).startswith(("http://", "https://")) else "[社内]"
            label = f"{prefix} {src}"
            if label not in seen:
                seen.append(label)
    return seen


def _collect_source_texts(step_results) -> List[str]:
    """各ステップの `source_texts`（出典本文）を重複排除して集約する。

    groundedness 検証用。表示用の `_collect_citations`（出典識別子）とは用途が
    異なる — 識別子（ファイル名）だけを検証器へ渡すと、どの主張も裏付けられず
    すべて neutral（支持率の分母 0）になってしまうため、本文を集めて渡す。

    `source_texts` を持たない経路（legacy agent 等）では空を返し、呼び出し側が
    従来の出典ラベルへフォールバックできるようにする。
    Web 側の同等処理は `_web_source_texts`。
    """
    seen: List[str] = []
    for sr in step_results or []:
        for text in getattr(sr, "source_texts", None) or []:
            if text and text not in seen:
                seen.append(text)
    return seen


def _citation_text(citation: str) -> str:
    """出典表示文字列（"[社内] xxx" / "[Web] xxx"）からラベルを外して中身を返す。"""
    return citation.split("] ", 1)[1] if "] " in citation else citation


def _merge_citations(internal: List[str], web: List[str]) -> List[str]:
    """内部出典と ⑤ の Web 出典を重複なく結合する。

    executor が動的 Web 検索を使った場合、同じ URL が内部側（"[Web] URL"）と
    ⑤ 側（"[Web] タイトル（URL）"）の両形式で並ぶため、URL の包含で重複排除する。
    """
    merged = list(internal)
    internal_texts = [_citation_text(c) for c in internal]
    for citation in web:
        if any(text and text in citation for text in internal_texts):
            continue
        merged.append(citation)
    return merged


def _web_citations(web_output: list) -> List[str]:
    """Web 検索結果（rag_search 互換 dict）から出典表示文字列を作る。"""
    cites: List[str] = []
    for entry in web_output or []:
        payload = entry.get("payload", {})
        title = payload.get("title") or "(無題)"
        url = payload.get("source") or ""
        cites.append(f"[Web] {title}（{url}）" if url else f"[Web] {title}")
    return cites


def _web_source_texts(web_output: list) -> List[str]:
    """Web 検索結果の本文（snippet/answer）を groundedness 検証用に抽出する。"""
    return [
        entry.get("payload", {}).get("answer", "")
        for entry in web_output or []
        if entry.get("payload", {}).get("answer")
    ]
