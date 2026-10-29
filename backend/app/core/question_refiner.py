"""0-(A) 質問の整理（LLM）— 問い合わせを「話題 → 観点」に整理し、回答用・社内検索用・Web 検索用の文を作る。

**すべての問い合わせで軽量 LLM を 1 回呼ぶ**（文字列一致で呼ぶかどうかを決めない）。

## なぜ必要か（実測 2026-10-22・Mac の ./run_dev.sh・基本版タブ）

次の 3 行を送ると、2 行目の「その件」が 1 行目を指すことが解決されず、3 行がそのまま
社内検索と Web 検索（SerpAPI）に渡っていた::

    Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？
    その件の経緯を詳しく教えて
    最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて

当時の 0-(A) の分解は、接続語 10 個の部分一致か「？」2 個以上のときだけ LLM を呼び、
この入力では呼ばれなかった。ここではその判定をせず、毎回 LLM で整理する
（当時の検知・分解・再構成は 2026-10-29 に `gates.py` から削除した）。

## 整理の単位

| 単位 | 意味 | 例（上の 3 行） |
|---|---|---|
| 話題 `Topic` | 対象（人・物・出来事）が同じ文のまとまり。**話題ごとに回答 1 つ** | 49ers の GM・ヘッドコーチの交代（1 つ） |
| 観点 `Aspect` | 話題の中の、知りたい側面。**観点ごとに社内検索・Web 検索** | 経緯 / 人事（2 つ） |
| 文 `Sentence` | 問い合わせの各文。どの観点に属し、前の文とどういう関係か | 文 1=主 / 文 2=同一 / 文 3=別観点 |

文どうしの関係（`relation`）:

| 値 | 意味 |
|---|---|
| `前置き` | 質問ではない文（状況の説明。「〜したい。」「〜をなくしました。」）。**観点を持たない**（aspect は空文字） |
| `主` | その話題で最初に質問している文 |
| `同一` | 前の文と同じ観点（指示語で指しているだけ・言い換え・「詳しく」の追加） |
| `別観点` | 同じ話題の、別の側面（原因と結果、手順と手数料 など） |

⚠️ `前置き` が無かった 2026-10-29 までは、質問ではない文も「主」にして観点を付けるしかなかった。
「住民票の写しを取りたい。その件の手数料は？」で、軽量モデルが 3 回とも「取得方法」と
「手数料」の 2 観点に分けた（定義どおりの出力で、禁止の文を足しても直らなかった）。

話題が独立している文（例: 住民票と天気）は別の `Topic` にする。

⚠️ **LLM が失敗したら元の文のまま進める**（`RefineResult.failed`）。回答は止めない。
⚠️ **`sentences[].text` は元の文の書き写し**であることを `gates._derives_from_query`
（文字 2-gram の一致率 0.5 以上）で確かめる。LLM が質問を作り出していたら採用しない。
⚠️ **`web_query` は問い合わせに書かれている語だけにする**（`restrict_web_query`）。LLM が知っていること
（実測: 「Jed York 49ers GM ヘッドコーチ **解任** 経緯」）を足すことがあり、プロンプトで禁止しても
3 回に 1 回は足した。空白で区切った語を 1 つずつ問い合わせと照らし、無い語を外す。

## 使われ方（2026-10-29〜）

`support_agent.run_support_agent_core` の 0-(A) が毎回呼ぶ。話題が複数なら利用者に選ばせ、
採用した話題を `Planner.create_plan_for_topic` に渡す（観点ごとに社内検索は `search_query`、
`wants_latest_web` の観点は `web_query` で Web 検索、回答生成は `answer_question`）。
⑤ Web フォールバックも観点ごとの `web_query` で検索する。
実際の LLM での出力の確認は `tests/usage/usage_refine_question.py`（`--repeat N`）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta
from typing import List, Literal, NamedTuple, Optional

from pydantic import BaseModel, Field, ValidationError

from backend.app.core.gates import _derives_from_query, judge_model

logger = logging.getLogger(__name__)

MAX_TOPICS = 4             # 1 つの問い合わせから取り出す話題の上限
MAX_ASPECTS_PER_TOPIC = 3  # 1 つの話題の観点の上限（観点ごとに社内検索するので増やしすぎない）

Relation = Literal["前置き", "主", "同一", "別観点"]


class Sentence(BaseModel):
    """問い合わせの 1 文と、その扱い。"""

    text: str = Field(..., description="問い合わせの文をそのまま書き写したもの")
    aspect: str = Field(..., description="この文が属する観点の name（前置きの文は空文字）")
    relation: Relation = Field(..., description="前置き / 主 / 同一 / 別観点")


class Aspect(BaseModel):
    """話題の中の 1 つの観点。観点ごとに検索する。"""

    name: str = Field(..., description="観点の短い名前（例: 経緯、人事、手数料）")
    search_query: str = Field(..., description="社内ナレッジ（意味検索）に使う 1 文")
    web_query: str = Field(..., description="Web 検索エンジンに入れる語句（問い合わせにある語を 8 語まで）")
    wants_latest_web: bool = Field(False, description="この観点について Web 検索・最新情報を求めているか")


class Topic(BaseModel):
    """1 つの話題。話題ごとに回答を 1 つ作る。"""

    answer_question: str = Field(..., description="回答担当者に渡す質問文")
    sentences: List[Sentence]
    aspects: List[Aspect]
    in_scope: Optional[bool] = Field(None, description="担当範囲内か（範囲が示されていなければ null）")


class RefinedQuestion(BaseModel):
    """LLM に返させる JSON 全体。"""

    topics: List[Topic]


class RefineResult(NamedTuple):
    """`refine_question` の戻り値。"""

    topics: List[Topic]   # 失敗時は元の文をそのまま入れた 1 話題・1 観点
    failed: bool          # LLM の失敗・形式違反で元の文へ戻したか
    reason: str           # failed のときの理由（ログ・ステップトレース用）
    elapsed_sec: float    # LLM 呼び出しにかかった秒数


# ⚠️ 例の中に、実際に試す問い合わせ（49ers の 3 行など）と同じ文を入れない。
#    入れると LLM が例の出力を写すだけになり、整理できるかの確認にならない
#    （tests/test_question_refiner.py が検査する）。
# <<TODAY>> / <<TOMORROW>> / <<SCOPE_BLOCK>> / <<QUERY>> は build_refine_prompt が埋める。
REFINE_PROMPT = """あなたは問い合わせを整理する担当です。利用者の問い合わせを読み、
文ごとに「どの話題の、どの観点か」を整理し、回答を作る担当者に渡す質問文と、検索に使う文を作ってください。
あなた自身は質問に答えません。

【今日の日付】
<<TODAY>>

<<SCOPE_BLOCK>>【用語】
- 話題（topic）: 対象（人・物・出来事）が同じ文のまとまり。話題ごとに回答を 1 つ作る。
- 観点（aspect）: 話題の中の、知りたい側面（例: 経緯、人事、手数料、手順）。観点ごとに検索する。
- 文どうしの関係（relation）:
  - 前置き: 質問ではない文。状況の説明（例: 「〜したい。」「〜をなくしました。」「〜を申し込みました。」）。
            観点を持たない（aspect は空文字 ""）。前置きの文から観点を作らない。
  - 主    : その話題で最初に質問している文
  - 同一  : 前の文と同じ観点（「その件」「それ」で前の文を指しているだけ・言い換え・「詳しく」などの注文の追加）
  - 別観点: 同じ対象の、別の側面（例: 原因と結果、手順と手数料）

【やること】
1. 問い合わせを文に分け、各文を書き写す（言い換えない）。
2. 対象が同じ文は 1 つの話題にまとめる。対象が独立している文だけ、別の話題にする
   （例: 住民票の取り方と、明日の天気は別の話題）。
   「A と B の違いは？」「A と B どちらが必要？」は 1 つの話題・1 つの観点。
3. 話題ごとに観点を決め、各文に relation と観点の name を付ける。
   観点は、質問している文（主・同一・別観点）で聞いていることだけから作る。
   同一の文は、前の文と同じ観点の name を付ける。前置きの文の aspect は空文字 "" にする。
4. 話題ごとに次を書く。
   - answer_question: 回答担当者に渡す質問文。
       ・「その件」「それ」などの指示語を、問い合わせの中の具体的な語に置き換える。
       ・すべての観点で聞かれていることを、漏らさず 1〜2 文にまとめる。
       ・「詳しく」「理由も」「手順で」など、答え方への注文は残す。
       ・「検索して」「調べて」などの、検索のしかたへの依頼は入れない（wants_latest_web で表す）。
       ・です・ます調の、そのまま読んで意味が通る日本語にする。
   - in_scope: 担当範囲が示されているときだけ、範囲内なら true、範囲外なら false。示されていなければ null。
5. 観点ごとに次を書く。
   - search_query: 社内ナレッジ（意味検索）に使う 1 文。その観点について何の情報が欲しいかを、
     依頼の言い回し（「教えてください」「検索して」）を除いた自然な文で書く（単語の羅列にしない）。
     「明日」「先月」などの相対的な日付は、今日の日付を基準に具体的な日付へ書き換える。
   - web_query: Web 検索エンジンに入れる語句。固有名詞と要点の語を空白で区切って 8 語まで。
     問い合わせの文に書かれている語だけを、書かれている形のまま使う（言い換えない・語を足さない）。
     例外: 相対的な日付は、今日の日付を基準にした「YYYY年M月D日」にする。
     問い合わせに無い語は、後で機械的に取り除かれる。
   - wants_latest_web: その観点の文が Web 検索・最新情報・ニュースを求めていれば true、そうでなければ false。
6. 上限: 話題は 4 つまで、1 話題の観点は 3 つまで。

【禁止】
- 問い合わせに書かれていない事実（人名・地名・日付・数値・組織名・経緯）を書き足さない。
  あなたが知っていることでも書かない。answer_question・search_query・web_query のすべてに当てはまる。
  - 対象の補足を書き足さない: 本拠地・所在地・正式名称・肩書き
    （例: チーム名に都市名を付ける、会社名に「株式会社」を付ける、人名に役職を付け足す）。
  - 出来事を推測して書き足さない: 問い合わせに無い「解任」「退任」「辞任」「逮捕」「買収」などの語を入れない。
- 聞かれていないことを足さない。
  - 「〜できますか？」「〜はありますか？」は、可否・有無だけを聞く（「方法も」「手続きも」「場所も」を足さない）。
- 指示語が何を指すか問い合わせの中から分からないときは、推測で埋めず、元の語のまま残す。
- 質問に答えない。説明・前置き・JSON 以外の文字を出力しない。

【例 1】同じ話題・観点 2 つ（同一の文と別観点の文がある）
問い合わせ:
市の図書館が開館時間を変更した理由は何ですか？
その件をもう少し詳しく
最新ニュースを検索して、新しい開館時間を教えて
出力:
{"topics": [{
  "answer_question": "市の図書館が開館時間を変更した理由を、詳しく教えてください。あわせて、変更後の新しい開館時間も教えてください。",
  "sentences": [
    {"text": "市の図書館が開館時間を変更した理由は何ですか？", "aspect": "変更の理由", "relation": "主"},
    {"text": "その件をもう少し詳しく", "aspect": "変更の理由", "relation": "同一"},
    {"text": "最新ニュースを検索して、新しい開館時間を教えて", "aspect": "新しい開館時間", "relation": "別観点"}],
  "aspects": [
    {"name": "変更の理由", "search_query": "市の図書館が開館時間を変更した理由",
     "web_query": "市 図書館 開館時間 変更 理由", "wants_latest_web": false},
    {"name": "新しい開館時間", "search_query": "市の図書館の変更後の開館時間",
     "web_query": "市 図書館 新しい開館時間", "wants_latest_web": true}],
  "in_scope": null}]}

【例 2】同じ話題・観点 1 つ（前置きの文 ＋ 指示語で前置きを指す質問）
問い合わせ: 印鑑登録証をなくしました。その場合の再発行の手続きは？
出力:
{"topics": [{
  "answer_question": "印鑑登録証をなくした場合の、再発行の手続きを教えてください。",
  "sentences": [
    {"text": "印鑑登録証をなくしました。", "aspect": "", "relation": "前置き"},
    {"text": "その場合の再発行の手続きは？", "aspect": "再発行の手続き", "relation": "主"}],
  "aspects": [
    {"name": "再発行の手続き", "search_query": "印鑑登録証をなくしたときの再発行の手続き",
     "web_query": "印鑑登録証 なくしました 再発行 手続き", "wants_latest_web": false}],
  "in_scope": null}]}

【例 3】独立した 2 つの話題
問い合わせ: 粗大ごみの出し方は？ また、明日の大阪の天気は？
出力:
{"topics": [
 {"answer_question": "粗大ごみの出し方を教えてください。",
  "sentences": [{"text": "粗大ごみの出し方は？", "aspect": "出し方", "relation": "主"}],
  "aspects": [{"name": "出し方", "search_query": "粗大ごみの出し方",
               "web_query": "粗大ごみ 出し方", "wants_latest_web": false}],
  "in_scope": null},
 {"answer_question": "<<TOMORROW>>の大阪の天気を教えてください。",
  "sentences": [{"text": "明日の大阪の天気は？", "aspect": "天気", "relation": "主"}],
  "aspects": [{"name": "天気", "search_query": "<<TOMORROW>>の大阪の天気",
               "web_query": "大阪 天気 <<TOMORROW>>", "wants_latest_web": true}],
  "in_scope": null}]}

【例 4】指示語が何を指すか分からない
問い合わせ: あの話はどうなりましたか
出力:
{"topics": [{
  "answer_question": "あの話はどうなりましたか？",
  "sentences": [{"text": "あの話はどうなりましたか", "aspect": "その後", "relation": "主"}],
  "aspects": [{"name": "その後", "search_query": "あの話はどうなったか",
               "web_query": "あの話", "wants_latest_web": false}],
  "in_scope": null}]}

【例 5】可否だけを聞いている（聞かれていないことを足さない）
問い合わせ: 図書館で駐車場は使えますか？
出力:
{"topics": [{
  "answer_question": "図書館で駐車場は使えますか？",
  "sentences": [{"text": "図書館で駐車場は使えますか？", "aspect": "駐車場の利用可否", "relation": "主"}],
  "aspects": [{"name": "駐車場の利用可否", "search_query": "図書館の駐車場を利用できるか",
               "web_query": "図書館 駐車場", "wants_latest_web": false}],
  "in_scope": null}]}

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


def original_as_topic(query: str) -> Topic:
    """失敗時に使う「元の文のまま」の 1 話題・1 観点。"""
    return Topic(
        answer_question=query,
        sentences=[Sentence(text=query, aspect="原文", relation="主")],
        aspects=[Aspect(name="原文", search_query=query, web_query=query, wants_latest_web=False)],
        in_scope=None,
    )


def _strip_code_fence(text: str) -> str:
    """```json ... ``` で囲まれていたら中身だけを返す。"""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def parse_refine_output(text: str, query: str) -> RefinedQuestion:
    """LLM の出力を検査して `RefinedQuestion` にする。不合格なら ValueError。

    - JSON として読めること、`RefinedQuestion` の形であること（relation は 前置き / 主 / 同一 / 別観点）
    - 話題が 1〜`MAX_TOPICS`、各話題の観点が 1〜`MAX_ASPECTS_PER_TOPIC`、文が 1 つ以上
    - answer_question / search_query / web_query / 観点名が空でないこと
    - 前置きでない文の aspect がその話題の観点のどれかであり、各観点に前置きでない文が 1 つ以上あること
      （前置きの文の aspect は見ない。前置きの文だけから作った観点は「文が無い」で落ちる）
    - 各話題で、前置きを除いた最初の文の relation が「主」であること（質問の文が無い話題は不合格）
    - 各文が元の問い合わせの書き写しであること（`_derives_from_query`）
    """
    try:
        parsed = RefinedQuestion.model_validate(json.loads(_strip_code_fence(text or "")))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(f"JSON の形式が違う: {type(e).__name__}") from e

    if not 1 <= len(parsed.topics) <= MAX_TOPICS:
        raise ValueError(f"話題の数が {len(parsed.topics)}（1〜{MAX_TOPICS} のはず）")
    for i, topic in enumerate(parsed.topics, start=1):
        if not 1 <= len(topic.aspects) <= MAX_ASPECTS_PER_TOPIC:
            raise ValueError(f"話題 {i} の観点の数が {len(topic.aspects)}（1〜{MAX_ASPECTS_PER_TOPIC} のはず）")
        if not topic.sentences:
            raise ValueError(f"話題 {i} に文が無い")
        if not topic.answer_question.strip():
            raise ValueError(f"話題 {i} の answer_question が空")
        names = [a.name for a in topic.aspects]
        for aspect in topic.aspects:
            for field in ("name", "search_query", "web_query"):
                if not getattr(aspect, field).strip():
                    raise ValueError(f"話題 {i} の観点の {field} が空")
        questions = [s for s in topic.sentences if s.relation != "前置き"]
        if not questions:
            raise ValueError(f"話題 {i} に質問の文が無い（前置きだけ）")
        if questions[0].relation != "主":
            raise ValueError(f"話題 {i} の前置きを除いた最初の文の relation が「主」でない")
        for sentence in topic.sentences:
            if not _derives_from_query(sentence.text, query):
                raise ValueError(f"元の問い合わせに無い文: {sentence.text[:40]}")
        for sentence in questions:
            if sentence.aspect not in names:
                raise ValueError(f"話題 {i} の文の観点「{sentence.aspect}」が観点の一覧に無い")
        used = {s.aspect for s in questions}
        for name in names:
            if name not in used:
                raise ValueError(f"話題 {i} の観点「{name}」に属する文が無い")
    return parsed


def restrict_web_query(web_query: str, query: str, *, today: date) -> str:
    """`web_query` から、問い合わせに書かれていない語を外す（LLM の出力を照らすだけ。問い合わせは解釈しない）。

    空白（全角を含む）で区切った語ごとに、`query` に部分文字列として含まれるかを見る。
    例外として、プロンプトで渡した今日・明日の日付（「YYYY年M月D日」）は残す（「明日」から書き換えたもの）。
    全部外れたら `query` をそのまま返す（以前の動き。Web 検索語が空になるよりよい）。

    ⚠️ 言い換えも外れる（「取りたい」→「取得」、「できますか」→「可否」）。検索語としては困らない。
    """
    allowed_dates = {_ja_date(today), _ja_date(today + timedelta(days=1))}
    words = web_query.split()
    kept = [w for w in words if w in query or w in allowed_dates]
    if len(kept) < len(words):
        dropped = [w for w in words if w not in kept]
        logger.info(f"[refine] web_query から問い合わせに無い語を外しました: {dropped}")
    return " ".join(kept) if kept else query


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
        RefineResult。LLM の失敗・形式違反のときは元の文 1 話題と failed=True。
        各観点の web_query は `restrict_web_query` を通したもの
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
                # 4 話題 × 3 観点の日本語 JSON。途中で切れると JSON が壊れて元の文へ戻るので余裕を持たせる
                "max_output_tokens": 3000,
            },
        )
        parsed = parse_refine_output(getattr(response, "text", "") or "", query)
        for topic in parsed.topics:
            for aspect in topic.aspects:
                aspect.web_query = restrict_web_query(aspect.web_query, query, today=today)
        return RefineResult(parsed.topics, False, "", time.time() - started)
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        logger.warning(f"[refine] 質問の整理に失敗したため元の文で進めます（{reason}）")
        return RefineResult([original_as_topic(query)], True, reason, time.time() - started)
