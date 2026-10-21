"""0-(A) 質問の整理（`refine_question`）を**実際の LLM** で試す確認用スクリプト。

使い方（リポジトリ直下で。別ターミナルで `ollama serve` を起動しておく）:

    PYTHONPATH=. uv run --no-sync python tests/usage/usage_refine_question.py            # 決めた 5 件を流して合否を出す
    PYTHONPATH=. uv run --no-sync python tests/usage/usage_refine_question.py --query "質問文"   # 任意の問い合わせを 1 件だけ
    PYTHONPATH=. uv run --no-sync python tests/usage/usage_refine_question.py --vertical gov    # 業界プロファイルの担当範囲つき

各ケースについて、LLM の出力（JSON）・所要秒数・合否の基準ごとの OK / NG を出す。
NG が 1 つでもあれば終了コード 1。「入力に無い語」の検査は英数字とカタカナ語だけを見るので、
漢字の人名・地名の書き足しは出力を目で確かめること。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# 設定（config/grace_config.yml）はリポジトリ直下から読む
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from backend.app.core.question_refiner import refine_question  # noqa: E402
from grace.config import get_config  # noqa: E402

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)

SCREEN_INPUT = (
    "Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？\n"
    "その件の経緯を詳しく教えて\n"
    "最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて"
)


def new_words(text: str, query: str) -> list:
    """`text` にあって `query` に無い英数字の語・カタカナ語（書き足しの疑い）。"""
    words = re.findall(r"[A-Za-z][A-Za-z0-9]*|[0-9]+|[ァ-ヴー]{2,}", text)
    allowed = f"{query} {TODAY.year} {TODAY.month} {TODAY.day} {TOMORROW.month} {TOMORROW.day}"
    return sorted({w for w in words if w not in allowed})


def no_new_words(topic, query):
    texts = [topic.answer_question] + [t for a in topic.aspects for t in (a.search_query, a.web_query)]
    found = new_words(" ".join(texts), query)
    return (not found, f"入力に無い英数字・カタカナ語が無い（見つかった語: {found or 'なし'}）")


def sentence_with(topic, word):
    """`word` を含む文（無ければ None）。"""
    return next((s for s in topic.sentences if word in s.text), None)


def aspect_of(topic, sentence):
    return next((a for a in topic.aspects if sentence and a.name == sentence.aspect), None)


def screen_checks(r, q):
    t = r.topics[0]
    s2, s3 = sentence_with(t, "その件"), sentence_with(t, "人事")
    a1, a3 = aspect_of(t, sentence_with(t, "背景")), aspect_of(t, s3)
    aq = t.answer_question
    return [
        (len(r.topics) == 1, "話題が 1 つ（3 文が同じ話題にまとまっている。画面で選択を求めない）"),
        (len(t.sentences) == 3, f"文が 3 つに分かれている（{len(t.sentences)} 個）"),
        (len(t.aspects) == 2, f"観点が 2 つ（経緯と人事）（{len(t.aspects)} 個: {[a.name for a in t.aspects]}）"),
        (s2 is not None and s2.relation == "同一", "文 2（その件の経緯を詳しく）の relation が「同一」"),
        (s3 is not None and s3.relation == "別観点", "文 3（49ers の新GM・ヘッドコーチの人事）の relation が「別観点」"),
        (a1 is not None and a1.wants_latest_web is False, "経緯の観点の wants_latest_web が false"),
        (a3 is not None and a3.wants_latest_web is True, "人事の観点の wants_latest_web が true"),
        ("その件" not in aq, "answer_question に「その件」が残っていない"),
        (any(w in aq for w in ("経緯", "背景")), "answer_question が経緯・背景を聞いている"),
        (any(w in aq for w in ("人事", "就任")), "answer_question が新 GM・ヘッドコーチの人事を聞いている"),
        ("詳しく" in aq, "answer_question に「詳しく」が残っている"),
        ("検索" not in aq, "answer_question に「検索して」が入っていない"),
        no_new_words(t, q),
    ]


# (名前, 問い合わせ, 検査を返す関数)
CASES = [
    ("画面の 3 行", SCREEN_INPUT, screen_checks),
    ("指示語（同じ行）", "住民票の写しを取りたい。その件の手数料はいくらですか？", lambda r, q: [
        (len(r.topics) == 1, "話題が 1 つ"),
        ("その件" not in r.topics[0].answer_question, "answer_question に「その件」が残っていない"),
        ("住民票" in r.topics[0].answer_question and "手数料" in r.topics[0].answer_question,
         "answer_question が住民票の写しの手数料を聞いている"),
        (all(not a.wants_latest_web for a in r.topics[0].aspects), "wants_latest_web がすべて false"),
        no_new_words(r.topics[0], q),
    ]),
    ("独立した 2 つの話題", "住民票の写しの取り方は？ また、明日の東京の天気は？", lambda r, q: [
        (len(r.topics) == 2, "話題が 2 つに分かれている"),
        (any(f"{TOMORROW.month}月{TOMORROW.day}日" in a.search_query
             for t in r.topics for a in t.aspects if "天気" in t.answer_question),
         f"天気の search_query で「明日」が {TOMORROW.month}月{TOMORROW.day}日 になっている"),
        (any(a.wants_latest_web for t in r.topics if "天気" in t.answer_question for a in t.aspects),
         "天気の wants_latest_web が true"),
        (all(not a.wants_latest_web for t in r.topics if "住民票" in t.answer_question for a in t.aspects),
         "住民票の wants_latest_web が false"),
    ]),
    ("1 文", "領収書は発行できますか？", lambda r, q: [
        (len(r.topics) == 1 and len(r.topics[0].aspects) == 1, "話題 1 つ・観点 1 つ"),
        (r.topics[0].aspects[0].wants_latest_web is False, "wants_latest_web が false"),
        no_new_words(r.topics[0], q),
    ]),
    ("手がかりの無い指示語", "その件の経緯を詳しく教えて", lambda r, q: [
        (len(r.topics) == 1, "話題が 1 つ"),
        no_new_words(r.topics[0], q),
        ("詳しく" in r.topics[0].answer_question, "answer_question に「詳しく」が残っている"),
    ]),
]


def show(name, query, result):
    print("=" * 72)
    print(f"■ {name}  （{result.elapsed_sec:.1f} 秒）")
    print(f"問い合わせ:\n{query}")
    if result.failed:
        print(f"⚠️ 整理に失敗 → 元の文のまま（理由: {result.reason}）")
    print("出力:")
    print(json.dumps({"topics": [t.model_dump() for t in result.topics]},
                     ensure_ascii=False, indent=2))


def run(query, profile, checks=None, name="指定の問い合わせ"):
    result = refine_question(query, get_config(), profile)
    show(name, query, result)
    if checks is None:
        return True
    rows = [(not result.failed, "整理に成功した（元の文に戻っていない）")]
    if not result.failed:
        try:
            rows += checks(result, query)
        except (IndexError, AttributeError) as e:
            rows.append((False, f"出力の形が想定と違い、検査できない（{type(e).__name__}）"))
    for ok, label in rows:
        print(f"  [{'OK' if ok else 'NG'}] {label}")
    return all(ok for ok, _ in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--query", help="任意の問い合わせを 1 件だけ試す（合否は出さない）")
    parser.add_argument("--vertical", help="業界プロファイル（gov / saas / ec）。担当範囲も判定させる")
    args = parser.parse_args()

    profile = None
    if args.vertical:
        from backend.app.core.verticals import PROFILES

        profile = PROFILES[args.vertical]

    print(f"モデル: {get_config().llm.light_model}  今日: {TODAY}")
    if args.query:
        run(args.query, profile)
        return 0

    results = [(name, run(query, profile, checks, name)) for name, query, checks in CASES]
    print("=" * 72)
    for name, ok in results:
        print(f"{'OK' if ok else 'NG'}  {name}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
