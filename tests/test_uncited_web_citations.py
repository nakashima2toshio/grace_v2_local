"""回答本文で引用していない Web 出典を、表示用の出典から外すことを守るテスト。

## なぜ必要か（実測 2026-10-04・Mac の E2E）

「サービスが落ちています」（saas）の回答本文は「参照情報には Web 検索結果も含まれて
いましたが、今回のご質問とは無関係の内容だったため、回答には使用していません」と
書いていたのに、出典欄には魚の「マス」の Wikipedia、TV 番組ページ、YouTube など
9 件の URL が並んだ。executor は Web で検索した URL を、回答に使ったかに関係なく
出典へ積むためである。

構成ルール 3・4（`grace/tools.py`）は Web の情報を使うとき URL を書き写させるので、
**本文に URL が無い Web 出典は回答に使われていない**とみなせる。

LLM・Qdrant・API キーには依存しない。
"""

import pytest

from backend.app.core.gates import drop_uncited_web_citations
from backend.app.core.support_agent import run_support_agent_core
from tests.conftest import PipelineStub, install_pipeline_stub

INTERNAL = "[社内] saas_docs.csv"
FISH = "[Web] https://ja.wikipedia.org/wiki/%E3%83%9E%E3%82%B9"
STATUS = "[Web] https://status.example.jp/"


class TestDropUncitedWebCitations:
    def test_uncited_web_citations_are_dropped_and_internal_kept(self):
        answer = "社内ナレッジ（saas_docs.csv）によると、ステータスページで確認できます。"

        assert drop_uncited_web_citations([INTERNAL, FISH, STATUS], answer) == [INTERNAL]

    def test_web_citation_copied_into_answer_is_kept(self):
        answer = "Web 検索結果（https://status.example.jp）によると、現在障害が発生しています。"

        assert drop_uncited_web_citations([INTERNAL, STATUS, FISH], answer) == [INTERNAL, STATUS]

    def test_fallback_format_with_title_is_matched_by_url(self):
        """⑤ Web フォールバックの形式「[Web] タイトル（URL）」も URL で照合する。"""
        cited = "[Web] 稼働状況（https://status.example.jp/incidents）"
        uncited = "[Web] マス - Wikipedia（https://ja.wikipedia.org/wiki/x）"
        answer = "Web 検索結果（https://status.example.jp/incidents）によると復旧済みです。"

        assert drop_uncited_web_citations([cited, uncited], answer) == [cited]

    @pytest.mark.parametrize("in_citation, in_answer", [
        ("https://ja.wikipedia.org/wiki/%E3%83%9E%E3%82%B9", "https://ja.wikipedia.org/wiki/マス"),
        ("https://ja.wikipedia.org/wiki/マス", "https://ja.wikipedia.org/wiki/%E3%83%9E%E3%82%B9"),
    ])
    def test_percent_encoding_differences_still_match(self, in_citation, in_answer):
        assert drop_uncited_web_citations([f"[Web] {in_citation}"], f"出典: {in_answer}") == [
            f"[Web] {in_citation}"
        ]

    def test_web_citation_without_url_is_kept(self):
        """URL を取り出せないものは判断できないので残す。"""
        assert drop_uncited_web_citations(["[Web] 無題"], "本文") == ["[Web] 無題"]

    @pytest.mark.parametrize("answer", [None, "", "Web 由来の回答です（出典の書き写しなし）"])
    def test_answer_citing_nothing_keeps_everything(self, answer):
        """どちらも引用していなければ判断できないので外さない。

        ⑤ Web フォールバックの回答を採用したのにモデルが URL を書かなかった場合に、
        Web 出典を消して使っていない社内の出典だけを残すと、かえって誤解を招く。
        """
        assert drop_uncited_web_citations([INTERNAL, FISH], answer) == [INTERNAL, FISH]

    def test_web_only_citations_kept_when_answer_cites_nothing(self):
        """社内 0 件・Web だけ（例: 天気）で URL を書かなかった回答も、出典を空にしない。"""
        assert drop_uncited_web_citations([FISH, STATUS], "明日は晴れです。") == [FISH, STATUS]


class TestSupportPipeline:
    def _run(self, monkeypatch, answer):
        stub = PipelineStub(
            answer=answer,
            # executor の出典（`_collect_citations` が [社内] / [Web] を付ける）
            sources=["saas_docs.csv", "https://ja.wikipedia.org/wiki/x", "https://status.example.jp"],
        )
        install_pipeline_stub(monkeypatch, stub)
        return run_support_agent_core("サービスが落ちています", use_web=False, do_action=False)

    def test_uncited_web_sources_are_not_shown(self, monkeypatch):
        result = self._run(monkeypatch, "社内ナレッジ（saas_docs.csv）によると、ステータスページを確認してください。")

        assert result.citations == ["[社内] saas_docs.csv"]

    def test_cited_web_source_is_shown(self, monkeypatch):
        result = self._run(
            monkeypatch,
            "社内ナレッジ（saas_docs.csv）と Web 検索結果（https://status.example.jp）によると…",
        )

        assert result.citations == ["[社内] saas_docs.csv", "[Web] https://status.example.jp"]
