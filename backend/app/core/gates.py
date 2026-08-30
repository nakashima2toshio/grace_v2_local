# backend/app/core/gates.py
"""回答ゲート・強制エスカレ・情報なし検知・救済などの純ロジック関数群。

`agent_support_example.py` から移設（React マイグレーション）。判定結果が
CLI 版と同一になるよう、ロジックは一切変更していない。後方互換のため
`agent_support_example` が再エクスポートする。
"""
from __future__ import annotations

import re
import sys
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from backend.app.core.verticals import (
    INTENT_MODEL,
    JUDGE_MAX_OUTPUT_TOKENS,
    MULTI_QUESTION_MAX_OUTPUT_TOKENS,
    ActionRequest,
    Decision,
    Intent,
    VerticalProfile,
)


def judge_model(config) -> str:
    """判定系（意図分類・情報なし判定・Review の分類）が使うモデル名を解決する。

    ⚠️ **`INTENT_MODEL` を直接使ってはいけない。**

    `INTENT_MODEL` は `config.py::get_default_ollama_model()`（＝環境変数
    `OLLAMA_DEFAULT_MODEL` かフォールバック文字列）を **import 時に**畳み込んだ
    モジュール定数で、`config/grace_config.yml` を一切見ない。一方 planner /
    reasoning / groundedness などは `grace/config.py` 経由で **yml の
    `llm.model` / `llm.light_model`** を読む。つまり解決経路が 2 本ある。

    このため両者は簡単に食い違う:

    - yml の `light_model` を書き換えても判定系には効かない
    - 環境変数を設定すると判定系だけが動き、yml 側は動かない

    実測 2026-08-17 02:12 の実行では、planner/検証器が `gemma4-e4b-ctx8k` で
    動いているのに判定系のログだけ `gemma4:e4b` を表示していた。派生元の
    `gemma4:e4b` は `num_ctx` が 4096（既定）で、8192 へ広げた派生モデルとは
    別物である。判定系のプロンプトは回答本文を丸ごと含むため、ここが 4096 だと
    枠を使い切って本文 0 文字（空応答）になりうる — `judges.enabled=true` へ
    戻したときに踏む罠である（`config.py::get_default_ollama_model()` の
    「既定が gemma4-e4b-ctx8k である理由」参照）。

    そこで**設定（yml）を正**とし、config から解決できないときだけ
    `INTENT_MODEL` へフォールバックする（`llm` を持たないテスト用スタブ向け）。
    """
    llm = getattr(config, "llm", None)
    return getattr(llm, "light_model", None) or INTENT_MODEL


def judges_enabled(config) -> bool:
    """補助 LLM 判定を呼んでよいか（`judges.enabled`）。

    ローカル LLM では 1 判定に 90〜250 秒かかるため、切れるようにしてある。
    無効時、各判定器は LLM を呼ばずに None を返し、呼び出し側が従来どおり
    安全側（キーワード判定）へ倒す。

    ⚠️ テスト用の config スタブには `judges` が無いことがあるため、
    属性が無ければ「有効」とみなす（既存テストの挙動を変えない）。
    """
    judges = getattr(config, "judges", None)
    if judges is None:
        return True
    return bool(getattr(judges, "enabled", True))


def multi_question_enabled(config) -> bool:
    """複数質問の構造解析を呼んでよいか（`judges.multi_question`）。

    ⚠️ **`judges.enabled` とは独立の専用フラグである。** 他の補助判定を切るのは
    「キーワード判定という同等の代替がある」からだが、複数質問の構造解析には
    代替が無い。切ると複数質問クエリの片方が**無言で落ちたまま、support_rate が
    高いので高信頼として提示される**（`docs/multi_question_handling.md` が最も
    危険とした事故）。そのためローカル LLM の既定（`judges.enabled=false`）でも
    こちらは有効にしてある。

    ⚠️ テスト用の config スタブには `judges` が無いことがあるため、
    属性が無ければ「有効」とみなす（`judges_enabled` と同じ扱い）。
    """
    judges = getattr(config, "judges", None)
    if judges is None:
        return True
    return bool(getattr(judges, "multi_question", True))


def create_intent_classifier(config) -> Callable[[str], Optional[Intent]]:
    """問い合わせ意図の LLM 分類器（軽量モデル・二段判定の第 2 段）を返す。

    返す関数は query を question / request / incident のいずれかへ分類する。
    分類できない場合（API エラー・想定外の出力）は None を返し、呼び出し側が
    安全側（従来のキーワード判定どおり）に倒す。呼び出しはキーワード候補が
    一致したときだけなので、追加コストは軽量モデル 1 呼び出しに限られる。

    `judges.enabled` が false の場合は LLM を呼ばず常に None を返す
    （＝キーワード判定のみ。ローカル LLM で 1 判定 90〜250 秒かかるため）。
    """
    if not judges_enabled(config):
        return lambda _query: None

    from grace.llm_compat import create_chat_client

    client = create_chat_client(config)
    model_name = judge_model(config)

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
                model=model_name,
                contents=prompt,
                config={"temperature": 0.0, "max_output_tokens": JUDGE_MAX_OUTPUT_TOKENS},
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


def _abbreviate_reason(text: str, limit: int = 120) -> str:
    """判定失敗の理由をログ 1 行に収まる長さへ縮める。"""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


# `create_no_info_judge` が判定できなかったときの種別。
# 呼び出し側はこれを見てログの文言を変える（「無効」と「失敗」は別物）。
JUDGE_DISABLED = "disabled"                  # judges.enabled=false（LLM を呼んでいない）
JUDGE_UNEXPECTED_OUTPUT = "unexpected_output"  # 応答したが answered/no_info を含まない
JUDGE_EXCEPTION = "exception"                # 例外（タイムアウト・接続断など）


def create_no_info_judge(
    config,
    on_failure: Optional[Callable[[str, str], None]] = None,
) -> Callable[[str, str], Optional[bool]]:
    """「情報なし回答」の LLM 判定器（軽量モデル・二段判定の第 2 段）を返す。

    返す関数は (query, answer) を受け、回答が質問の中心的な事柄に実質的に
    答えていれば False（answered）、「見つからない・お問い合わせください」に
    留まるなら True（no_info）を返す。判定できない場合（API エラー・想定外の
    出力）は None を返し、呼び出し側が安全側（escalate）に倒す。呼び出しは
    NO_INFO_MARKERS が一致したとき、または出典が Web のみ（社内根拠ゼロ）の
    回答に限られるので、追加コストは軽量モデル 1 呼び出しに留まる。

    `judges.enabled` が false の場合は LLM を呼ばず常に None を返す
    （＝マーカー一致のみで判定。呼び出し側は安全側へ倒す）。

    ⚠️ **このゲートが見るのは「実質的な内容があるか」だけ。** 内容の
    確実性・不確実性は判定材料ではない。④' は「情報なし回答検知ゲート」であって
    不確実性ゲートではないので、**中身のある回答を「予測だから」で有人対応へ
    倒してはならない**（不確実な話題そのものを有人へ回したいなら、それは ④ の
    強制エスカレ＝キーワード／intent の仕事）。

    grace_v2 実測 2026-08-17 16:17: 「明日の東京の天気は？」に対し天気・最高 30℃・
    最低 22℃・降水確率まで具体的に答え groundedness 1.00（17/17 supported）
    だった回答が `no_info` と判定され、エスカレされた。判定基準に
    「質問が将来の予測を求めており、回答が確定情報ではなく…注記つき」という
    条件があり、**天気予報は定義上どうやってもこれに当たる**ためである
    （予測質問 × 標準的な注記 ⇒ 内容によらず no_info）。判定条件を
    「予測の中身を示せているか」へ寄せ、注記の有無では判定しないと明示した。

    ⚠️ **これはプロンプトの論理の誤りなので、LLM がローカルでも同じように起きる。**

    Args:
        on_failure: 判定できなかったときに `(kind, detail)` を受け取るコールバック。
            `kind` は `JUDGE_DISABLED` / `JUDGE_UNEXPECTED_OUTPUT` /
            `JUDGE_EXCEPTION` のいずれか。

            ⚠️ **理由を実行記録に残すために必要。** 以前は理由を
            `sys.stderr` へ print するだけだったので、`emit` 経由の実行ログ
            （UI・SSE）には `判定失敗` という結果しか出ず、**なぜ失敗したのかを
            後から追えなかった**（実測「明日の東京の天気は？」で
            `[no-info] 実質回答判定（gemma4:e4b）: 判定失敗` とだけ出た）。

            出典が Web のみの回答は `force_judge=True` で判定が必須になり、
            判定失敗は安全側の escalate に倒れる。つまり判定器が失敗し続けると
            **Web フォールバックで得た回答は内容によらず必ず有人対応へ回る**。
            この状態に陥っているかを判断するには失敗理由が要る。

            None なら従来どおり stderr へ出す（CLI の挙動を変えない）。
    """
    def _fail(kind: str, detail: str) -> None:
        if on_failure is not None:
            on_failure(kind, detail)
        else:
            print(f"   [no-info] {detail} → 安全側（escalate）", file=sys.stderr)

    if not judges_enabled(config):
        # ⚠️ **「無効」と「失敗」を同じ None で返しつつ、理由では区別する。**
        # 無効時も None を返す仕様は変えない（呼び出し側の安全側判断を保つ）が、
        # ログに理由が出ないと「判定器が壊れている」と読めてしまう。実際には
        # 設定で切ってあるだけで、LLM は一度も呼ばれていない。
        def _disabled_judge(_query: str, _answer: str) -> Optional[bool]:
            _fail(JUDGE_DISABLED, "判定器が無効（judges.enabled=false）のため実行せず")
            return None

        return _disabled_judge

    from grace.llm_compat import create_chat_client

    client = create_chat_client(config)
    model_name = judge_model(config)

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
            "  また、質問が将来の予測・見通しを求めているのに、回答が予測そのものを示さず、\n"
            "  要望・検討段階の情報の紹介に留まる場合も no_info\n"
            "  （不確実な見通しは有人対応に回すべきため）。\n"
            "  ⚠️ ただし、質問が予測を求めていて、回答が**具体的な予測の内容（日付・数値・\n"
            "  見込みの中身）を示している場合は answered**。予測に「確定した内容ではない」\n"
            "  「最新情報は各提供元でご確認ください」等の注記が付くのは当然であり、\n"
            "  注記の有無で no_info にしてはならない。判定は「質問された事柄そのものの\n"
            "  内容を示せているか」だけで行う。\n\n"
            "判定例:\n"
            "- 質問「返品規定を教えて」に、一般的な返品ルール（30日以内・法定8日等）を提示し、\n"
            "  末尾で「弊社固有の規定は見当たりませんでした」と断る回答 → answered\n"
            "- 質問「送料はいくら？」に、一般的な料金の目安表を提示する回答 → answered\n"
            "- 質問「〜とはどんな制度ですか？」に、公的サイトを根拠として制度の目的・対象・\n"
            "  手続きを説明する回答 → answered\n"
            "- 質問「明日の東京の天気は？」に、天気・最高最低気温・降水確率を具体的に示し、\n"
            "  末尾で「最新の予報は各提供元でご確認ください」と注記する回答 → answered\n"
            "  （予測の中身を示しているため。予測であること・注記があることは減点しない）\n"
            "- 質問「この商品の入荷予定日は？」に、日付を一切示せず、「商品ページで確認できる\n"
            "  場合がある」等の一般的な確認方法の案内と問い合わせ先への誘導のみの回答 → no_info\n"
            "- 質問「来年の〜の予測は？」に、確定情報ではない要望・検討段階の情報を紹介し、\n"
            "  「正式に確定した内容ではない」と注記する回答 → no_info\n\n"
            f"質問: {query}\n\n回答:\n{answer}\n\n"
            "出力（answered / no_info のいずれか 1 語のみ）:"
        )
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"temperature": 0.0, "max_output_tokens": JUDGE_MAX_OUTPUT_TOKENS},
            )
            text = (response.text or "").strip().lower()
            if "no_info" in text or "no-info" in text:
                return True
            if "answered" in text:
                return False
            # LLM は応答したが answered/no_info を含まない（書式不履行・空応答）。
            # 空応答はローカル LLM が思考だけ返して打ち切られた場合に起きるので、
            # 「空」と「別の文字列」を区別できるよう本文をそのまま載せる。
            _fail(JUDGE_UNEXPECTED_OUTPUT,
                  f"想定外の判定出力: {_abbreviate_reason(repr(text))}")
        except Exception as e:
            # 例外（タイムアウト・接続断など）は回答の質とは無関係のインフラ障害。
            # 型名だけでは接続断とタイムアウトを取り違えるのでメッセージも残す。
            _fail(JUDGE_EXCEPTION,
                  f"実質回答判定に失敗（{type(e).__name__}: "
                  f"{_abbreviate_reason(str(e))}）")
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

    ⚠️ **`force_judge` は「判定せよ」というトリガであって、判定結果ではない。**
    判定が得られなかった（`None`）とき、候補句も一致していなければ
    **escalate しない**。ここを escalate に倒すと「出典が Web のみ ⇒ 常に有人
    対応」という無条件ルールになり、`force_judge` を足したときの設計意図
    （＝候補句が無い回答も *判定に掛ける*）から外れる。

    本リポジトリの既定は `judges.enabled=false`（ローカル LLM では 1 判定に
    90〜250 秒かかるため意図的に切ってある）なので、判定は**常に**得られない。
    つまりここを escalate にしていると、Web フォールバックで得た回答は内容に
    よらず全件が有人対応へ回っていた（実測 2026-08-17 01:22 の実行）。

    候補句が一致している場合は従来どおり判定不能を escalate に倒す（第 1 段の
    キーワード判定が既に「情報なし回答らしい」と言っているため）。

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
    if verdict is None and marker is None:
        # force_judge だけで呼ばれ、判定が得られなかった。
        # 判定に掛けた結果ではないので、Web のみを理由に escalate しない。
        return False, None
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


def _should_rescue_unverified(
    decision: Decision,
    verification_failed: bool,
    has_contradiction: bool,
    citation_count: int,
    answer: str,
) -> bool:
    """**検証器そのものが落ちた**ときに、生成済みの回答を escalate から救うか。

    `_answer_gate` は `verified=False` を一律 escalate にするが、その
    `verified=False` には性質の違う 2 つが混ざっている:

      (a) 検証は動いたが主張を肯定できなかった  → escalate で正しい
      (b) **検証 LLM が例外・タイムアウト・空応答で判定できなかった**
          → 回答の質とは無関係のインフラ障害

    ローカル LLM では検証 1 回に 90〜250 秒かかり (b) が常態化する。実測では
    16:07:10 に 107 文字の正しい Web 回答が生成されたのに、16:11:43 の検証
    タイムアウトだけを理由に破棄され、空の内部回答で escalate していた
    （＝**答えを作れているのに答えない**）。

    そこで (b) に限り、安全側の条件をすべて満たすときだけ未確認注記つきの
    answer として残す:
      - gate 判定が escalate（answer なら救済不要）
      - 検証器の失敗が原因である（`verification_failed`）。(a) は対象外
      - 矛盾が検出されていない（矛盾ありは安全側に倒し escalate 継続）
      - 出典が 1 件以上あり、回答本文が空でない

    ⚠️ 「情報なし回答」の除外はここでは行わない。救済後も後段の ④' ゲート
       （`_detect_no_info_answer`）を必ず通るため、そこで捕捉される。
    """
    if decision != "escalate" or not verification_failed:
        return False
    return not (has_contradiction or citation_count == 0 or not answer)


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


def _contradicted_claims(gres, limit: int = 5, max_chars: int = 160) -> List[str]:
    """groundedness 結果から「矛盾」と判定された主張の本文を取り出す。

    ⚠️ **件数だけでは誤検知を切り分けられない。** 矛盾が 1 件でもあると
    executor は `answer_conf` を 0.30 に cap する。誤検知ならば正しい回答の
    信頼度を不当に下げることになるので、どの主張が矛盾と判定されたのかを
    ログ・イベントに残して後から検証できるようにする
    （実測「明日の東京の天気は？」では `contradicted=1` としか出ず追跡できなかった）。

    `claims` を持たない結果（旧シリアライズ・テスト用スタブ）でも落ちないよう
    getattr で取り出す。表示用なので件数と長さに上限を設ける。
    """
    claims = getattr(gres, "claims", None) or []
    out: List[str] = []
    for claim in claims:
        if getattr(claim, "verdict", None) != "contradicted":
            continue
        text = " ".join(str(getattr(claim, "claim", "") or "").split())
        if not text:
            continue
        out.append(text if len(text) <= max_chars else text[:max_chars] + "…")
        if len(out) >= limit:
            break
    return out


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


# =============================================================================
# 複数質問クエリ（docs/multi_question_handling.md §13）
# =============================================================================
#
# 1 つの入力に複数の質問が含まれるとき、主質問を 1 つ選んで答え、採用しなかった
# 主質問は明示して返す（絞り込み方式）。ここは検知・構造解析・再構成の 3 つの
# 純ロジックを提供し、**選択そのもの（HITL）とパイプラインへの組み込みは
# support_agent 側の責務**とする。
#
# ⚠️ **安全側の向きが、このファイルの他の判定器と逆である。**
#
#   | 機構                       | 判定できないとき |
#   |----------------------------|------------------|
#   | `_detect_no_info_answer` 等 | escalate（答えない方が安全） |
#   | **複数質問検知**            | **「単一とみなす」**（＝現行動作を維持） |
#
# 誤って分解する方が害が大きいため。単一質問クエリの挙動は 1 ミリも変えない
# （docs/multi_question_handling.md §6・§13.6）。

# 第 1 段（候補検出）で見る接続表現。ここに一致しなければ LLM は呼ばない。
MULTI_QUESTION_MARKERS = (
    "また、",
    "また ",
    "さらに",
    "加えて",
    "併せて",
    "あわせて",
    "ところで",
    "それと",
    "もう一つ",
    "もうひとつ",
)

# 疑問符がこの数以上あれば、接続表現が無くても第 2 段へ回す
# （「A は？ B は？」のように接続詞なしで並ぶ書き方に対応）。
MULTI_QUESTION_MIN_MARKS = 2

# 過剰分解の上限。これを超えるクラスタは信用せず「単一とみなす」へ倒す
# （分解が暴走したときに選択肢が大量に出るのを防ぐ）。
MAX_QUESTION_CLUSTERS = 4


def _count_question_marks(query: str) -> int:
    """全角・半角の疑問符を数える。"""
    return (query or "").count("？") + (query or "").count("?")


def looks_like_multi_question(query: str) -> bool:
    """第 1 段: 複数質問の**候補**か（LLM 呼び出しゼロ）。

    ここが False なら第 2 段は呼ばれず、現行フローがそのまま走る。
    `_detect_no_info_answer` の第 1 段（`NO_INFO_MARKERS` の部分一致）と同じ役割。

    ⚠️ **「？」の数だけで判定してはいけない。** 「A と B の違いは？」は疑問符 1 つの
    単一質問であり、「住民票の取り方は？ その手数料は？」は疑問符 2 つだがクラスタは
    1 つ（主質問 1 ＋ 関連質問 1）である。ここは**候補検出**に徹し、
    最終的な構造判断は第 2 段に任せる。
    """
    if not query or not query.strip():
        return False
    if _match_keyword(query, MULTI_QUESTION_MARKERS) is not None:
        return True
    return _count_question_marks(query) >= MULTI_QUESTION_MIN_MARKS


def _is_explicit_single(text: str) -> bool:
    """モデルが「単一質問である」と明示的に答えたか。

    形式違反（前置き・了解の返事）と区別するために要る。どちらも
    `_parse_cluster_output` は None を返すが、前者は正常、後者は**やり直す
    価値がある**（実測 2026-08-29: 解析器が解析結果ではなく「了解しました。
    ルールを理解しました」と返し、複数質問が単一扱いになった）。
    """
    return bool(text) and "single" in text.strip().lower()[:12]


def _char_bigrams(text: str) -> set:
    """空白を除いた文字 2-gram の集合。語彙・形態素解析に依存しない。"""
    compact = "".join(text.split())
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


# 主質問・関連質問が元の問い合わせに由来しているとみなす最低一致率。
# 分解は「元の文を切り分ける」作業なので、出力は元の文と大半の文字を共有する。
# 0.5 は言い換え（「取り方」→「取得方法」等）を許しつつ、無関係な散文を落とす。
MIN_QUERY_OVERLAP = 0.5


def _derives_from_query(line: str, query: str) -> bool:
    """`line` が `query` を切り分けたものとみなせるか。

    ⚠️ **これが無いと、モデルの散文がそのまま「主質問」になる。**
    `_parse_cluster_output` は行を機械的に読むので、前置きが 2〜4 行なら
    そのまま採用され、利用者が聞いていない「質問」に答え、UI にも
    「主質問」として表示される。実測 2026-08-29（クラウド版）で解析器が
    返したのは解析結果ではなく了解の返事だった。今回はたまたま行数が
    上限を超えて弾かれたが、行数が少なければ通っていた。
    """
    line_grams = _char_bigrams(line)
    if len(line_grams) < 2:
        return False
    query_grams = _char_bigrams(query)
    if not query_grams:
        return False
    overlap = len(line_grams & query_grams) / len(line_grams)
    return overlap >= MIN_QUERY_OVERLAP


def _parse_cluster_output(text: str, query: str) -> Optional[List[Tuple[str, List[str]]]]:
    """第 2 段の LLM 出力を `[(main, [related...]), ...]` へ解析する純関数。

    期待する形式（1 行 1 クラスタ・`|` で主質問と関連質問を区切る）::

        住民票の写しの取り方は？ | その手数料は？
        他の市町村に住民票を移動する方法は？

    JSON ではなく行区切りにしたのは、軽量モデルでも崩れにくいためである
    （`grace/llm_compat` の JSON モードは Anthropic 側で使えるが、判定 1 回に
    スキーマを積むより行フォーマットの方が失敗率が低い）。

    解析できない・単一とみなすべき場合は **None** を返す。呼び出し側は
    None を「単一質問」として扱う（安全側 = 現行動作の維持）。
    """
    if not text or not text.strip():
        return None

    # モデルが「SINGLE」等を返した場合は単一として扱う
    if _is_explicit_single(text):
        return None

    clusters: List[Tuple[str, List[str]]] = []
    for line in text.splitlines():
        line = line.strip().lstrip("-・*0123456789. ").strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        main = parts[0].strip()
        if not main:
            continue
        related = [p for p in parts[1:] if p]
        clusters.append((main, related))

    if not clusters:
        return None
    # クラスタが 1 つでも、関連質問があるなら意味がある（再構成に使う）。
    # 主質問 1 つ・関連質問 0 は「単一質問」と同義なので None を返す。
    if len(clusters) == 1 and not clusters[0][1]:
        return None
    if len(clusters) > MAX_QUESTION_CLUSTERS:
        # 過剰分解。信用せず単一へ倒す（§13.6）
        return None
    # ⚠️ **元の問い合わせに由来しない行が 1 つでもあれば、出力全体を捨てる。**
    # 部分採用は「散文の一部が主質問になる」最悪の形。1 行でも怪しければ
    # 単一質問として現行フローへ倒すほうが安全側（§13.6）。
    for main, related in clusters:
        if not all(_derives_from_query(part, query) for part in [main, *related]):
            return None
    return clusters


class QuestionAnalysis(NamedTuple):
    """0-(A) 第 2 段の結果。

    clusters: `[(主質問, [関連質問...]), ...]`。単一質問・判定不能なら None。
    verdicts: 主質問ごとの `範囲内か`。判定していない・怪しいときは None
        （呼び出し側は別途スコープ分類器へ回すか、全件を範囲内として扱う）。
    """

    clusters: Optional[List[Tuple[str, List[str]]]]
    verdicts: Optional[List[bool]]


# 行頭の担当範囲ラベル（`IN: …` / `OUT: …`）。
_SCOPE_PREFIX_RE = re.compile(r"^\s*(IN|OUT)\s*[:：]\s*(.*)$", re.IGNORECASE)


def _split_scope_prefix(text: str) -> Tuple[str, Optional[List[bool]]]:
    """`IN:` / `OUT:` 付きの出力を、ラベルを外した本文と判定へ分ける。

    ⚠️ **1 行でもラベルが無ければ判定は捨てる（None）。** 部分的に解釈して
    一部だけ断ると、聞かれた質問が黙って消える。判定を捨てても本文（分解）は
    使えるので、スコープだけ別の呼び出しへ回せばよい（安全側）。
    """
    if not text or not text.strip():
        return text, None
    if _is_explicit_single(text):
        return text, None

    body: List[str] = []
    verdicts: List[bool] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        matched = _SCOPE_PREFIX_RE.match(line)
        if not matched:
            return text, None          # ラベルが揃っていない → 判定は使わない
        verdicts.append(matched.group(1).upper() == "IN")
        body.append(matched.group(2))
    if not verdicts:
        return text, None
    return "\n".join(body), verdicts


def create_question_analyzer(
    config,
    profile: Optional[VerticalProfile] = None,
) -> Callable[[str], QuestionAnalysis]:
    """0-(A) 第 2 段の解析器を返す（**分解と担当範囲判定を 1 回で**）。

    返す関数は query を `QuestionAnalysis(clusters, verdicts)` へ解析する。

    ⚠️ **プロファイルを渡すと LLM 呼び出しが 1 回で済む。** 以前は分解と
    スコープ判定で 2 回呼んでおり、実測 2026-08-30（ローカル LLM）で
    16.3 秒 ＋ 2.2 秒かかっていた（前者はモデルのウォームアップ込み）。
    往復を 1 回に畳んで 2.2 秒分を落とす。

    判定（`verdicts`）が取れなければ None を返す。呼び出し側は
    `create_scope_classifier` で従来どおり判定するか、全件を範囲内として扱う。

    ⚠️ **判定できないときは None（＝単一質問として現行フローへ）に倒す。**
    このファイルの他の判定器（`create_no_info_judge` 等）が「判定不能なら
    escalate」に倒すのとは**向きが逆**である。誤って質問を分解すると、
    利用者が聞いていない質問に答えたり、不要な選択を求めたりするため、
    「何もしない」方が安全側になる（§13.6）。

    ⚠️ **`config` が None のときは LLM を呼ばず常に None を返す**（＝第 1 段の
    キーワード判定のみで動く）。テストの config スタブや、LLM を使わせたくない
    経路でも単一質問の挙動が変わらないことを保証する。
    """
    if config is None:
        return lambda _query: QuestionAnalysis(None, None)

    from grace.llm_compat import create_chat_client

    client = create_chat_client(config)
    model_name = judge_model(config)
    # 担当範囲が分かるときだけ、同じプロンプトで IN/OUT も出させる。
    scope = profile.scope_description if profile else ""
    scope_name = profile.name if profile else ""

    def build_prompt(query: str, strict: bool) -> str:
        # ⚠️ **「入力: … 出力:」の穴埋め形式だけに頼らない。**
        # 実測 2026-08-29（クラウド版・claude-haiku-4-5-20251001）で、モデルは
        # 解析結果ではなく「了解しました。…ルールを理解しました」と返した。
        # 規則の羅列＋穴埋めは「指示を受け取った」と解釈されうるので、
        # 末尾で**やることを命令文で言い切る**。
        reminder = (
            "\n⚠️ 直前の応答は形式に違反していた。了解・確認・説明を書かず、"
            "結果の行だけを出力すること。\n"
            if strict else ""
        )
        # 担当範囲が分かるときは、同じ 1 回の呼び出しで IN/OUT も判定させる。
        scope_block = (
            f"【担当範囲（{scope_name}）】\n{scope}\n\n" if scope else ""
        )
        format_lines = (
            "- 1 行に 1 つの主質問。行頭に IN:（担当範囲内）か OUT:（範囲外）を付ける\n"
            "- 関連質問がある場合は | で続ける\n"
            "- 主質問が 1 つで関連質問も無い場合は SINGLE とだけ出力\n"
            "- 説明・前置き・番号は出力しない\n\n"
            if scope else
            "- 1 行に 1 つの主質問。関連質問がある場合は | で続ける\n"
            "- 主質問が 1 つで関連質問も無い場合は SINGLE とだけ出力\n"
            "- 説明・前置き・番号は出力しない\n\n"
        )
        example = (
            "【例】\n"
            "入力: 住民票の写しの取り方は？ また、明日の東京の天気は？\n"
            "出力:\n"
            "IN: 住民票の写しの取り方は？\n"
            "OUT: 明日の東京の天気は？\n\n"
            "入力: 住民票の写しの取り方は？ その手数料は？\n"
            "出力:\n"
            "IN: 住民票の写しの取り方は？ | その手数料は？\n\n"
            "入力: 住民票と戸籍謄本の違いは？\n"
            "出力:\n"
            "SINGLE\n\n"
            if scope else
            "【例】\n"
            "入力: 住民票の写しの取り方は？ また、他の市町村に住民票を移動する方法は？\n"
            "出力:\n"
            "住民票の写しの取り方は？\n"
            "他の市町村に住民票を移動する方法は？\n\n"
            "入力: 住民票の写しの取り方は？ その手数料は？\n"
            "出力:\n"
            "住民票の写しの取り方は？ | その手数料は？\n\n"
            "入力: 住民票と戸籍謄本の違いは？\n"
            "出力:\n"
            "SINGLE\n\n"
        )
        return (
            "あなたは問い合わせの構造を解析する担当です。"
            "次の問い合わせに含まれる質問を「主質問」と「関連質問」に整理"
            + ("し、それぞれが担当範囲内かを判定" if scope else "")
            + "してください。\n\n"
            + scope_block
            + "【定義】\n"
            "- 主質問  : それ単体で意味が通る、独立したトピックの質問\n"
            "- 関連質問: 直前の主質問の文脈が無いと意味が通らない従属質問\n"
            "            （例:「その手数料は？」「必要な持ち物は？」）\n\n"
            "【出力形式】\n"
            + format_lines
            + "【重要】\n"
            "- 「A と B の違いは？」は**1 つの比較質問**。分解しない → SINGLE\n"
            "- 「A と B、どちらが必要ですか？」も**1 つの選択質問** → SINGLE\n"
            "- 「手続きと持ち物を教えて」は 1 つの手続きの 2 側面 → SINGLE\n"
            "- 疑問符の数で数えない。**トピックが独立しているか**で判断する\n\n"
            + example
            + "【指示】\n"
            "次の問い合わせを解析し、上の【出力形式】に従って結果の行だけを"
            "出力すること。了解・確認・前置き・ルールの復唱は書かない。\n"
            f"{reminder}\n"
            f"問い合わせ: {query}\n"
            "結果:"
        )

    def ask(query: str, strict: bool) -> str:
        response = client.models.generate_content(
            model=model_name,
            contents=build_prompt(query, strict),
            config={"temperature": 0.0,
                    "max_output_tokens": MULTI_QUESTION_MAX_OUTPUT_TOKENS},
        )
        return response.text or ""

    def analyze(query: str) -> QuestionAnalysis:
        try:
            raw = ask(query, strict=False)
            body, verdicts = _split_scope_prefix(raw)
            clusters = _parse_cluster_output(body, query)
            if clusters is None and not _is_explicit_single(raw):
                # 形式違反（散文・空）＝**やり直す価値がある**。SINGLE と明示された
                # ときは正常な判定なので、ここでトークンを使わない。
                # 追加の 1 回は第 1 段が一致した問い合わせでしか起きない。
                print("   [multi-q] 第 2 段の応答が形式に従っていないため 1 回だけ"
                      f"再要求します（応答: {_abbreviate_reason(raw) or '空'}）",
                      file=sys.stderr)
                raw = ask(query, strict=True)
                body, verdicts = _split_scope_prefix(raw)
                clusters = _parse_cluster_output(body, query)
            if clusters is None:
                # ⚠️ **黙って単一へ倒さない。** 第 1 段が一致した（＝複数質問らしい）
                # のに第 2 段が単一と判断したときは、何を返したのかが分からないと
                # 原因を追えない。実測 2026-08-29 で、解析器が呼ばれたのに単一扱いに
                # なり、ログが 1 行も無くて切り分けできなかった。
                print(f"   [multi-q] 第 2 段は単一と判断（応答: "
                      f"{_abbreviate_reason(raw) or '空'}）", file=sys.stderr)
            # 分解できなければ判定も使わない（何に対する IN/OUT か決まらない）。
            if clusters is None or (verdicts is not None
                                    and len(verdicts) != len(clusters)):
                verdicts = None
            return QuestionAnalysis(clusters, verdicts)
        except Exception as e:
            print(f"   [multi-q] 構造解析に失敗（{type(e).__name__}: "
                  f"{_abbreviate_reason(str(e))}）→ 単一質問として継続",
                  file=sys.stderr)
        return QuestionAnalysis(None, None)

    return analyze


def create_cluster_analyzer(
    config,
) -> Callable[[str], Optional[List[Tuple[str, List[str]]]]]:
    """構造解析だけを行う解析器（担当範囲は判定しない）。

    `create_question_analyzer(config)` の薄い別名。担当範囲を判定しない経路
    （基本版タブ・プロファイル未指定）と、分解だけを見たい呼び出し向け。
    """
    analyzer = create_question_analyzer(config)
    return lambda query: analyzer(query).clusters


def detect_question_clusters(
    query: str,
    analyzer: Optional[Callable[[str], Optional[List[Tuple[str, List[str]]]]]] = None,
) -> List[Tuple[str, List[str]]]:
    """複数質問の二段判定。`[(主質問, [関連質問...]), ...]` を返す。

    第 1 段: `looks_like_multi_question`（接続表現・疑問符の数）。不一致なら
    LLM を呼ばず空リストを返す。
    第 2 段: `analyzer`（軽量 LLM）で構造解析。

    Returns:
        クラスタのリスト。**空リストなら「単一質問として現行どおり処理せよ」**の意。
        要素が 1 つでも、関連質問を持つ場合は再構成の対象になる（§13.3）。
    """
    if not looks_like_multi_question(query):
        return []
    if analyzer is None:
        return []
    clusters = analyzer(query)
    return clusters or []


def analyze_questions(
    query: str,
    analyzer: Optional[Callable[[str], QuestionAnalysis]] = None,
) -> QuestionAnalysis:
    """複数質問の二段判定（分解＋担当範囲）。`QuestionAnalysis` を返す。

    第 1 段: `looks_like_multi_question`（接続表現・疑問符の数）。不一致なら
    LLM を呼ばず `QuestionAnalysis(None, None)` を返す。
    第 2 段: `analyzer`（軽量 LLM）。1 回の呼び出しで分解と IN/OUT を得る。

    `detect_question_clusters` の上位互換。分解だけが要るなら従来どおり
    そちらを使ってよい（安全側の向きは同じ）。
    """
    if not looks_like_multi_question(query) or analyzer is None:
        return QuestionAnalysis(None, None)
    return analyzer(query)


def fallback_reconstruct(main: str, related: List[str]) -> str:
    """再構成の素朴なフォールバック（LLM 不要）。

    LLM が使えない・失敗したときに使う。主質問と関連質問を素直に連結するだけで、
    **指示語は解決されない**（「その手数料」は「その手数料」のまま）。それでも
    主質問の文脈が同じクエリ内に入るぶん、関連質問を単体で投げるよりは
    ベクトル検索が効く。

    ⚠️ 単語の羅列にはしない。`grace/planner.py` が「自然言語の文脈を維持せよ」と
    求めており（`planner.py:103-105`）、羅列に落とすとベクトル検索の精度が下がる。
    """
    main = (main or "").strip()
    parts = [p.strip() for p in (related or []) if p and p.strip()]
    if not parts:
        return main
    return main + " " + " ".join(parts)


def reconstruct_query(
    main: str,
    related: List[str],
    config=None,
) -> str:
    """採用クラスタ（主質問 ＋ 関連質問）を、自然言語の 1 文へ再構成する。

    設計: `docs/multi_question_handling.md` §13.3。

    ## なぜ再構成するのか

    1. **指示語を解決するため。** 「**その**手数料は？」は単体では何の手数料か
       不明で、ベクトル検索がまったく効かない。主質問の文脈を埋め込む必要がある。
    2. **別トピックのノイズを落とすため。** 原文をそのまま渡すと、採用しなかった
       主質問の文字列が残り、検索の意味の重心がボケる（§1 の #2 と同じ問題）。

    ## `planner.py` の「完全一致でコピー」規則と衝突しない理由

    `grace/planner.py:110-111` が禁じているのは**要約・キーワード化・分割**であり、
    `:103-105` はむしろ「単語の羅列に変換せず、**自然言語の文脈を維持**せよ」と
    求めている。再構成は自然言語の 1 文を保つ変換であり、この意図に沿う。

    かつ **再構成はパイプラインの外側（前処理）で行う。**
    `run_support_agent_core(query=<再構成後の質問>)` として渡すため、planner から
    見れば再構成後の文が「ユーザーの元の質問文」であり、それを完全一致でコピーする。
    **planner・executor・gates の判定ロジックは一切改変しない。**

    Args:
        main: 主質問
        related: 主質問に従属する関連質問（空なら LLM を呼ばない）
        config: LLM 設定。None のときは LLM を呼ばず `fallback_reconstruct` を使う

    Returns:
        再構成後の質問文。**呼び出し側は原文とは別に保持すること**
        （再構成は LLM 依存で誤りうるため、利用者が検証できる必要がある。§13.5）。
    """
    main = (main or "").strip()
    parts = [p.strip() for p in (related or []) if p and p.strip()]

    # 関連質問が無ければ再構成の必要がない。**LLM を呼ばない**（コストゼロ）。
    if not parts:
        return main

    # config が無い・`judges.multi_question` が false なら LLM を呼べない。
    # 素朴な連結へフォールバックする（指示語は解決されないが検索は効く）。
    if config is None or not multi_question_enabled(config):
        return fallback_reconstruct(main, parts)

    from grace.llm_compat import create_chat_client

    try:
        client = create_chat_client(config)
        prompt = (
            "次の主質問と関連質問を、自然な 1 文の質問へまとめてください。\n\n"
            "【ルール】\n"
            "- 関連質問に含まれる指示語（「その」「それ」等）は、主質問の内容へ置き換える\n"
            "- 内容を要約・省略しない。すべての要素を残す\n"
            "- 単語の羅列にしない。自然な日本語の文にする\n"
            "- 質問文だけを出力する。説明・前置きは書かない\n\n"
            "【例】\n"
            "主質問: 住民票の写しの取り方は？\n"
            "関連質問: その手数料は？\n"
            "出力: 住民票の写しの取り方と、その手数料を教えてください\n\n"
            f"主質問: {main}\n"
            f"関連質問: {' / '.join(parts)}\n"
            "出力:"
        )
        response = client.models.generate_content(
            model=judge_model(config),
            contents=prompt,
            config={"temperature": 0.0,
                    "max_output_tokens": MULTI_QUESTION_MAX_OUTPUT_TOKENS},
        )
        text = (response.text or "").strip()
        if text:
            return text
        print("   [multi-q] 再構成が空応答 → 素朴な連結でフォールバック",
              file=sys.stderr)
    except Exception as e:
        print(f"   [multi-q] 再構成に失敗（{type(e).__name__}: "
              f"{_abbreviate_reason(str(e))}）→ 素朴な連結でフォールバック",
              file=sys.stderr)

    return fallback_reconstruct(main, parts)


def deferred_main_questions(
    clusters: List[Tuple[str, List[str]]],
    adopted_index: int,
) -> List[str]:
    """採用しなかったクラスタの**主質問**を返す。

    🔴 **この戻り値は必ず利用者へ提示すること。**
    提示しないと「片方の質問が無言で落ち、しかも `support_rate` が高いため
    高信頼として提示される」という、本設計が最も危険とした事故
    （`docs/multi_question_handling.md` §概要）と区別がつかなくなる。

    関連質問は主質問に従属しており、主質問を保留すれば一緒に保留される。
    そのため主質問だけを列挙すれば足りる。
    """
    return [
        main
        for i, (main, _related) in enumerate(clusters)
        if i != adopted_index
    ]


# =============================================================================
# 0-(A) スコープ判定（担当範囲外の主質問を選択肢に出さない）
# =============================================================================
#
# 複数質問のうち片方が業界の担当範囲外のとき、利用者に選択を求めるのは筋が悪い。
# 範囲外の質問は `verticals.SCOPE_POLICY` により生成側で「断って窓口案内」する
# のが正しい扱いで、選ばせても答えは変わらないからである。
#
# 実測 2026-08-29:「住民票の写しの取り方は？ ところで、明日の東京の天気は？」で、
# 天気（gov の範囲外）が選択肢に並び、利用者に 1 往復させたうえ、
# 保留として落とされた。同じ質問を選択なしで通したクラウド版は、
# 住民票に回答しつつ天気は「担当範囲外です → 気象庁へ」と 1 パスで返している。
#
# ⚠️ **安全側の向きは「判定できないなら範囲内」である。** 範囲外と誤判定すると
# 答えられる質問を断ってしまう。答えようとして生成側の SCOPE_POLICY が断る分には
# 二重の防波堤が働くだけで害がない。


def _parse_scope_output(text: str, count: int) -> Optional[List[bool]]:
    """スコープ判定の LLM 出力を `[範囲内か, ...]` へ解析する純関数。

    期待する形式（1 行 1 問・入力の順序どおり）::

        1: IN
        2: OUT

    行数が問い数と一致しない、IN/OUT を含まない行があるなど、少しでも
    解釈が怪しければ **None**（＝判定不能＝全件範囲内として扱う）を返す。
    部分的に解釈して一部だけ断る、という中途半端な結果を作らない。
    """
    if not text or not text.strip() or count <= 0:
        return None

    verdicts: List[bool] = []
    for line in text.splitlines():
        token = line.strip().upper()
        if not token:
            continue
        # 「1: OUT」「- OUT」「OUT」いずれも受ける。OUT を先に見る
        # （"OUT" は "IN" を含まないが、順序を固定して読み違いを防ぐ）。
        if "OUT" in token:
            verdicts.append(False)
        elif "IN" in token:
            verdicts.append(True)
        else:
            return None

    if len(verdicts) != count:
        return None
    return verdicts


def create_scope_classifier(
    config,
    profile: Optional[VerticalProfile] = None,
) -> Callable[[List[str]], Optional[List[bool]]]:
    """主質問が業界の担当範囲内かを判定する分類器（第 2 段）を返す。

    返す関数は主質問のリストを受け、`[範囲内か, ...]` を返す。判定できない
    場合は **None**（呼び出し側は全件を範囲内として扱う）。

    **1 リクエストにつき LLM 呼び出しは 1 回**。全主質問をまとめて 1 回の
    プロンプトで判定する（主質問ごとに呼ぶと、ローカル LLM では待ち時間が
    主質問の数だけ積み上がる）。

    次のいずれかでは LLM を呼ばず常に None を返す:
      - `config` が None
      - `judges.multi_question` が false（0-(A) 全体のスイッチ）
      - プロファイル未指定、または `scope_description` が空（基本版タブ）
    """
    if config is None or not multi_question_enabled(config):
        return lambda _questions: None
    if profile is None or not profile.scope_description:
        return lambda _questions: None

    from grace.llm_compat import create_chat_client

    client = create_chat_client(config)
    model_name = judge_model(config)
    scope = profile.scope_description
    name = profile.name

    def classify(questions: List[str]) -> Optional[List[bool]]:
        if not questions:
            return None
        listed = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
        prompt = (
            f"あなたは「{name}」の問い合わせ窓口の担当者です。\n\n"
            f"【担当範囲】\n{scope}\n\n"
            "【判定】\n"
            "次の各質問が担当範囲内か範囲外かを判定してください。\n"
            "- 担当範囲内 → IN\n"
            "- 担当範囲外（天気・ニュース・一般常識・他機関の手続き等） → OUT\n\n"
            "【出力形式】\n"
            "- 質問の番号順に 1 行ずつ「番号: IN」または「番号: OUT」だけを出力する\n"
            "- 説明・前置きは出力しない\n\n"
            "【例】\n"
            "1. 住民票の写しの取り方は？\n"
            "2. 明日の東京の天気は？\n"
            "出力:\n"
            "1: IN\n"
            "2: OUT\n\n"
            f"{listed}\n"
            "出力:"
        )
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"temperature": 0.0,
                        "max_output_tokens": MULTI_QUESTION_MAX_OUTPUT_TOKENS},
            )
            return _parse_scope_output(response.text or "", len(questions))
        except Exception as e:
            print(f"   [multi-q] スコープ判定に失敗（{type(e).__name__}: "
                  f"{_abbreviate_reason(str(e))}）→ 全件を担当範囲内として継続",
                  file=sys.stderr)
        return None

    return classify


def scope_classifier_for(
    analysis: QuestionAnalysis,
    fallback: Callable[[], Callable[[List[str]], Optional[List[bool]]]],
) -> Callable[[List[str]], Optional[List[bool]]]:
    """`split_by_scope` へ渡す分類器を選ぶ。

    解析器が担当範囲まで返していれば **LLM を呼ばずにその判定を使う**
    （0-(A) の往復が 2 回 → 1 回になる）。返していなければ `fallback()` が
    作る分類器で従来どおり判定する。

    ⚠️ **`fallback` は遅延生成（引数なしの関数）で受ける。** 分類器の生成は
    LLM クライアントの構築を伴うため、使わないのに毎回作ると
    「必要になってから作る」という二段判定の狙いが崩れる。
    """
    verdicts = analysis.verdicts
    if verdicts is not None:
        return lambda questions: (
            list(verdicts) if len(questions) == len(verdicts) else None
        )
    return fallback()


def split_by_scope(
    clusters: List[Tuple[str, List[str]]],
    classify: Optional[Callable[[List[str]], Optional[List[bool]]]] = None,
) -> Tuple[List[int], List[int]]:
    """クラスタを「担当範囲内」「担当範囲外」の添字へ分ける。

    Returns:
        `(in_scope_indexes, out_of_scope_indexes)`。判定器が無い・判定できない
        場合は **全件が範囲内**（＝現行動作。誤って断らない）。

    ⚠️ **全件が範囲外と判定されたときも全件を範囲内として返す。** 分類器が
    壊れている（すべて OUT を返す）のと、本当に全部範囲外なのを区別できず、
    前者だと利用者の質問が丸ごと消える。全部範囲外なら、生成側の SCOPE_POLICY が
    従来どおり断るので二重には守られている。
    """
    all_in = (list(range(len(clusters))), [])
    if not clusters or classify is None:
        return all_in

    verdicts = classify([main for main, _related in clusters])
    if verdicts is None or len(verdicts) != len(clusters):
        return all_in

    in_scope = [i for i, ok in enumerate(verdicts) if ok]
    out_scope = [i for i, ok in enumerate(verdicts) if not ok]
    if not in_scope:
        return all_in
    return in_scope, out_scope


def answer_cites_sources(answer: Optional[str], citations: List[str]) -> bool:
    """回答本文が、出典として渡ったファイル名・URL に触れているか。

    構成ルール 4 は「出典行をそのまま書き写す」ことを求めているが、従うかは
    モデル次第である。実測 2026-08-30（ローカル LLM）では、前回まで本文に
    あった「出典: gov_faq.csv」が消えた（出典セクションは別に出るので実害は
    小さいが、**揺れていること自体が見えていなかった**）。

    ⚠️ **これはゲートではない。** 落ちても回答は止めない。観測できるように
    するための判定である（止めると、出典を本文に書かないだけの正しい回答まで
    捨てることになる）。

    Args:
        answer: 生成された回答本文
        citations: `[社内] gov_faq.csv` のようなラベル付き出典

    Returns:
        1 件でも本文から参照できていれば True。出典が無い場合も True
        （書きようがないため「守れていない」とは言わない）。
    """
    if not citations:
        return True
    if not answer:
        return False
    for citation in citations:
        body = _citation_text(citation).strip()
        if body and body in answer:
            return True
    return False


# 「回答本文が既に担当範囲外に触れているか」を見る語。
#
# ⚠️ **モデルの言い回しは揃わない。** 断りの言い方（「担当範囲外です」
# 「お答えできません」「対応しておりません」）は生成のたびに変わるので、
# 語で緩く拾う。拾えなければ下の `ensure_out_of_scope_notice` が追記するだけで、
# 二重に書かれることはあっても情報が欠けることはない。
OUT_OF_SCOPE_ANSWER_MARKERS = (
    "担当範囲外",
    "対応範囲外",
    "範囲外です",
    "範囲外となります",
    "お答えできません",
    "お答えいたしかね",
    "扱っておりません",
    "取り扱っておりません",
)


def ensure_out_of_scope_notice(
    answer: Optional[str],
    questions: List[str],
    guidance: str = "",
    links: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """担当範囲外の質問への断りが回答本文に無ければ追記する。

    ## なぜモデル任せにしないか

    0-(A) は範囲外の主質問を検索クエリから外し、その質問文を業務方針として
    生成側へ渡して「同じ回答の中で断れ」と指示している。しかし**指示に従うかは
    モデル次第**である。

    実測 2026-08-29（同一の質問・同一の注入）:

    | モデル | 回答本文の断り |
    |---|---|
    | claude-sonnet-4-6 | あり（「天気・気象情報は当窓口の担当範囲外」） |
    | gemma4:26b-a4b-it-qat | **なし**（住民票にだけ答えて終わり） |

    ローカル LLM が落としたのは、回答生成プロンプトの【回答の構成ルール】1
    （参照情報にある事実のみ）・7（捏造禁止）と衝突して見えるためと考えられる。
    プロンプト側でも例外だと明示したが、それでも従う保証はない。

    「聞いたはずの片方が返答に出てこない」のは利用者から見て事故なので、
    **プロバイダに依存せず必ず出る**ようにここで担保する。

    Args:
        answer: 生成された回答本文
        questions: 担当範囲外と判定した主質問
        guidance: 添える窓口案内（業界プロファイル由来）
        links: 案内先の URL（表示名 → URL。業界プロファイル由来）。
            **「窓口へどうぞ」で終わらせない。** 利用者は結局そこから自分で
            探すことになる（実測 2026-08-30 の指摘「あるけど、URL ぐらい欲しい」）。

    Returns:
        追記後の回答。追記不要ならそのまま返す（同一オブジェクト）。
    """
    if not questions or not answer or not answer.strip():
        return answer
    if any(marker in answer for marker in OUT_OF_SCOPE_ANSWER_MARKERS):
        return answer   # モデルが自分で断っている

    listed = "\n".join(f"- {q}" for q in questions)
    note = guidance or "該当する窓口へお問い合わせください。"
    link_lines = ""
    if links:
        link_lines = "\n\n" + "\n".join(
            f"- {label}: {url}" for label, url in links.items()
        )
    return (
        f"{answer.rstrip()}\n\n"
        "---\n\n"
        "**担当範囲外のご質問について**\n\n"
        f"{listed}\n\n"
        f"上記は当窓口の担当範囲外のためお答えできません。{note}{link_lines}"
    )
