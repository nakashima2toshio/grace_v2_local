"""0-(A) 質問の整理（LLM）— 問い合わせを回答用・社内検索用・Web 検索用の文へ書き直す。

**すべての問い合わせで軽量 LLM を 1 回呼ぶ**（文字列一致で呼ぶかどうかを決めない）。

## なぜ必要か（実測 2026-10-22・Mac の ./run_dev.sh）

基本版タブに次の 3 行を送ると、2 行目の「その件」が 1 行目を指すことが解決されず、
3 行がそのまま社内検索と Web 検索（SerpAPI）に渡っていた::

    Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？
    その件の経緯を詳しく教えて
    最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて

0-(A) の既存の分解（`gates.create_question_analyzer`）は、接続語 10 個の部分一致か
「？」2 個以上（`gates.looks_like_multi_question`）のときだけ呼ばれ、この入力では
呼ばれなかった。ここではその判定をせず、毎回 LLM で整理する。

## 出力（話題ごとに 1 項目・最大 4 項目）

| 項目 | 使う場所 |
|---|---|
| `answer_question` | 回答生成（reasoning）に渡す質問文。指示語を解決し、「詳しく」等の注文は残す |
| `search_query` | 社内検索（Qdrant の意味検索）。依頼の言い回しを除いた自然な 1 文 |
| `web_query` | Web 検索エンジン。固有名詞と要点の語を空白区切りで 3〜8 語 |
| `wants_latest_web` | 「最新ニュースを検索して」等、Web・最新情報を求めているか |
| `in_scope` | 業界プロファイルの担当範囲内か（プロファイルが無ければ None） |

⚠️ **LLM が失敗したら元の文のまま進める**（`RefineResult.failed`）。回答は止めない。
⚠️ **main / related は元の文の書き写し**であることを `gates._derives_from_query`
（文字 2-gram の一致率 0.5 以上）で確かめる。LLM が質問を作り出していたら採用しない。

⚠️ 2026-10-22 時点では **support_agent にはまだ組み込んでいない**。プロンプトを実際の
LLM で確かめる段階で、確認用スクリプトは `tests/usage/usage_refine_question.py`。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta
from typing import List, NamedTuple, Optional

from pydantic import BaseModel, Field, ValidationError

from backend.app.core.gates import _derives_from_query, judge_model

logger = logging.getLogger(__name__)

# 1 つの問い合わせから取り出す話題の上限（gates.MAX_QUESTION_CLUSTERS と同じ）
MAX_REFINED_ITEMS = 4


class RefinedItem(BaseModel):
    """整理した話題 1 つ分。"""

    main: str = Field(..., description="その話題の中心となる質問。問い合わせの文をそのまま書き写す")
    related: List[str] = Field(default_factory=list, description="同じ話題に属する残りの文（書き写し）")
    answer_question: str = Field(..., description="回答担当者に渡す質問文")
    search_query: str = Field(..., description="社内ナレッジ（意味検索）に使う 1 文")
    web_query: str = Field(..., description="Web 検索エンジンに入れる語句")
    wants_latest_web: bool = Field(False, description="Web 検索・最新情報を求めているか")
    in_scope: Optional[bool] = Field(None, description="担当範囲内か（範囲が示されていなければ null）")


class RefinedQuestion(BaseModel):
    """LLM に返させる JSON 全体。"""

    questions: List[RefinedItem]


class RefineResult(NamedTuple):
    """`refine_question` の戻り値。"""

    items: List[RefinedItem]   # 失敗時は元の文をそのまま入れた 1 項目
    failed: bool               # LLM の失敗・形式違反で元の文へ戻したか
    reason: str                # failed のときの理由（ログ・ステップトレース用）
    elapsed_sec: float         # LLM 呼び出しにかかった秒数


# ⚠️ 例の中に、実際に試す問い合わせ（49ers の 3 行など）と同じ文を入れない。
#    入れると LLM が例の出力を写すだけになり、整理できるかの確認にならない
#    （tests/test_question_refiner.py が検査する）。
# <<TODAY>> / <<TOMORROW>> / <<SCOPE_BLOCK>> / <<QUERY>> は build_refine_prompt が埋める。
REFINE_PROMPT = """あなたは問い合わせを整理する担当です。利用者の問い合わせを読み、
(1) 回答を作る担当者に渡す質問文、(2) 社内ナレッジを検索する文、(3) Web を検索する語句、
の 3 つに書き直してください。あなた自身は質問に答えません。

【今日の日付】
<<TODAY>>

<<SCOPE_BLOCK>>【やること】
1. 問い合わせに含まれる話題を分ける。
   - 同じ話題の言い換え・追加の問い（「その件」「それ」「詳しく」「あわせて」など）は 1 つの話題にまとめる。
   - 話題が独立している場合だけ、別の項目にする（例: 住民票の取り方と、明日の天気）。
   - 「A と B の違いは？」「A と B どちらが必要？」は 1 つの話題。
   - 項目は最大 4 つ。
2. 項目ごとに次を書く。
   - main: その話題の中心となる質問。問い合わせの文をそのまま書き写す（言い換えない）。
   - related: 同じ話題に属する残りの文。問い合わせの文をそのまま書き写す。無ければ空の配列。
   - answer_question: 回答担当者に渡す質問文。
       ・「その件」「それ」などの指示語を、問い合わせの中の具体的な語に置き換える。
       ・main と related で聞かれていることを、漏らさず 1〜2 文にまとめる。
       ・「詳しく」「理由も」「手順で」など、答え方への注文は残す。
       ・「検索して」「調べて」などの、検索のしかたへの依頼は入れない（wants_latest_web で表す）。
       ・です・ます調の、そのまま読んで意味が通る日本語にする。
   - search_query: 社内ナレッジ（意味検索）に使う 1 文。
       ・answer_question から「教えてください」などの依頼の言い回しを除き、
         何についての情報が欲しいかを表す自然な文にする（単語の羅列にしない）。
       ・「明日」「先月」などの相対的な日付は、今日の日付を基準に具体的な日付へ書き換える。
   - web_query: Web 検索エンジンに入れる語句。固有名詞と要点の語を空白で区切って 3〜8 語。
   - wants_latest_web: 問い合わせが Web 検索・最新情報・ニュースを求めていれば true、そうでなければ false。
   - in_scope: 担当範囲が示されているときだけ、範囲内なら true、範囲外なら false。示されていなければ null。

【禁止】
- 問い合わせに書かれていない事実（人名・地名・日付・数値・組織名・経緯）を書き足さない。
  あなたが知っていることでも書かない。
- 指示語が何を指すか問い合わせの中から分からないときは、推測で埋めず、元の語のまま残す。
- 質問に答えない。説明・前置き・JSON 以外の文字を出力しない。

【例 1】
問い合わせ:
市の図書館が開館時間を変更した理由は何ですか？
その件をもう少し詳しく
最新ニュースを検索して、新しい開館時間を教えて
出力:
{"questions": [{
  "main": "市の図書館が開館時間を変更した理由は何ですか？",
  "related": ["その件をもう少し詳しく", "最新ニュースを検索して、新しい開館時間を教えて"],
  "answer_question": "市の図書館が開館時間を変更した理由を、詳しく教えてください。あわせて、変更後の新しい開館時間も教えてください。",
  "search_query": "市の図書館が開館時間を変更した理由と、変更後の開館時間",
  "web_query": "市立図書館 開館時間 変更",
  "wants_latest_web": true,
  "in_scope": null}]}

【例 2】
問い合わせ: 印鑑登録証をなくしました。その場合の再発行の手続きは？
出力:
{"questions": [{
  "main": "印鑑登録証をなくしました。",
  "related": ["その場合の再発行の手続きは？"],
  "answer_question": "印鑑登録証をなくした場合の、再発行の手続きを教えてください。",
  "search_query": "印鑑登録証を紛失したときの再発行手続き",
  "web_query": "印鑑登録証 紛失 再発行",
  "wants_latest_web": false,
  "in_scope": null}]}

【例 3】
問い合わせ: 粗大ごみの出し方は？ また、明日の大阪の天気は？
出力:
{"questions": [
 {"main": "粗大ごみの出し方は？", "related": [],
  "answer_question": "粗大ごみの出し方を教えてください。",
  "search_query": "粗大ごみの出し方", "web_query": "粗大ごみ 出し方",
  "wants_latest_web": false, "in_scope": null},
 {"main": "明日の大阪の天気は？", "related": [],
  "answer_question": "<<TOMORROW>>の大阪の天気を教えてください。",
  "search_query": "<<TOMORROW>>の大阪の天気", "web_query": "大阪 天気 <<TOMORROW>>",
  "wants_latest_web": true, "in_scope": null}]}

【例 4】
問い合わせ: あの話はどうなりましたか
出力:
{"questions": [{
  "main": "あの話はどうなりましたか", "related": [],
  "answer_question": "あの話はどうなりましたか？",
  "search_query": "あの話はどうなったか", "web_query": "あの話 どうなった",
  "wants_latest_web": false, "in_scope": null}]}

【問い合わせ】
<<QUERY>>

上の【やること】と【禁止】に従い、JSON だけを出力してください。"""


def _ja_date(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def build_refine_prompt(query: str, *, today: date, profile=None) -> str:
    """プロンプトを組み立てる（LLM は呼ばない）。"""
    scope = getattr(profile, "scope_description", "") if profile else ""
    scope_block = f"【担当範囲（{profile.name}）】\n{scope}\n\n" if scope else ""
    return (
        REFINE_PROMPT
        .replace("<<TODAY>>", _ja_date(today))
        .replace("<<TOMORROW>>", _ja_date(today + timedelta(days=1)))
        .replace("<<SCOPE_BLOCK>>", scope_block)
        .replace("<<QUERY>>", query)
    )


def _original_as_item(query: str) -> RefinedItem:
    """失敗時に使う「元の文のまま」の 1 項目。"""
    return RefinedItem(
        main=query, related=[], answer_question=query, search_query=query,
        web_query=query, wants_latest_web=False, in_scope=None,
    )


def _strip_code_fence(text: str) -> str:
    """```json ... ``` で囲まれていたら中身だけを返す。"""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def parse_refine_output(text: str, query: str) -> RefinedQuestion:
    """LLM の出力を検査して `RefinedQuestion` にする。不合格なら ValueError。

    - JSON として読めること、`RefinedQuestion` の形であること
    - 項目が 1〜`MAX_REFINED_ITEMS` 個であること
    - answer_question / search_query / web_query が空でないこと
    - main・related が元の問い合わせの書き写しであること（`_derives_from_query`）
    """
    try:
        parsed = RefinedQuestion.model_validate(json.loads(_strip_code_fence(text or "")))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(f"JSON の形式が違う: {type(e).__name__}") from e
    items = parsed.questions
    if not 1 <= len(items) <= MAX_REFINED_ITEMS:
        raise ValueError(f"項目数が {len(items)}（1〜{MAX_REFINED_ITEMS} のはず）")
    for item in items:
        for name in ("answer_question", "search_query", "web_query"):
            if not getattr(item, name).strip():
                raise ValueError(f"{name} が空")
        for part in [item.main, *item.related]:
            if not _derives_from_query(part, query):
                raise ValueError(f"元の問い合わせに無い文: {part[:40]}")
    return parsed


def refine_question(query: str, config, profile=None, *, today: Optional[date] = None,
                    client=None) -> RefineResult:
    """問い合わせを LLM で整理する。**毎回 1 回** LLM を呼ぶ。

    Args:
        query: 利用者の問い合わせ（原文）
        config: GRACE 設定（`judge_model(config)` のモデルを使う）
        profile: 業界プロファイル（あれば担当範囲も判定させる）
        today: 今日の日付（テスト用。省略時は date.today()）
        client: LLM クライアント（テスト用。省略時は create_chat_client(config)）

    Returns:
        RefineResult。LLM の失敗・形式違反のときは元の文 1 項目と failed=True
    """
    today = today or date.today()
    prompt = build_refine_prompt(query, today=today, profile=profile)
    started = time.time()
    try:
        if client is None:
            from grace.llm_compat import create_chat_client

            client = create_chat_client(config)
        response = client.models.generate_content(
            model=judge_model(config),
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": RefinedQuestion,
                "temperature": 0.0,
                # 4 項目 × 5 欄の日本語 JSON。途中で切れると JSON が壊れて元の文へ戻るので余裕を持たせる
                "max_output_tokens": 2000,
            },
        )
        parsed = parse_refine_output(getattr(response, "text", "") or "", query)
        return RefineResult(parsed.questions, False, "", time.time() - started)
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        logger.warning(f"[refine] 質問の整理に失敗したため元の文で進めます（{reason}）")
        return RefineResult([_original_as_item(query)], True, reason, time.time() - started)
