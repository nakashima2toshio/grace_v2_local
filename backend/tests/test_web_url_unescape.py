# backend/tests/test_web_url_unescape.py
r"""Web 検索結果の `\uXXXX` エスケープを**境界で戻す**ことを固定するテスト。

## 背景（実測 2026-08-17 16:17）

引用一覧と reasoning プロンプトの【参照情報】に、エスケープされたままの URL が
載っていた。

    https://www.toshin.com/weather/detail?id=56682
    https://tenki.jp/forecast/3/16/4410/13103/10days_detail.html?code=130010&lang=jp

`=` は `=`、`&` は `&`。この文字列は

  1. リンクとして開けない（クエリが壊れている）
  2. LLM へ渡す【参照情報】の出典表示にも壊れた形で入る
  3. `_merge_citations` の URL 包含による重複排除の一致精度を落とす

`requests.json()` は正しい JSON エスケープを復号するので、ここまで literal で
残るのは**上流（SerpAPI の link 値）が二重エスケープされている**ため。上流の癖を
待つのではなく、`_parse_to_rag_format`（rag_search 互換フォーマットへの変換）
＝外部データがシステムへ入る境界で正規化する。

## `codecs.decode(s, "unicode_escape")` を使わない理由

あれは latin-1 経由の復号なので、**日本語のタイトル・スニペットを壊す**
（「東京」→ 化けた文字列）。`\uXXXX` の並びだけを対象にする。

ここで固定すること:
  1. URL のエスケープが戻ること（`=` / `&`）
  2. タイトル・スニペットも戻ること（同じ経路に載る）
  3. **日本語が壊れないこと**（unicode_escape 誤用の回帰防止）
  4. duckduckgo / serpapi / google_cse の全バックエンドで効くこと
  5. 引用表示（`[Web] タイトル（URL）`）に正規化後の値が載ること
  6. エスケープが無い文字列・空文字・非文字列で壊れないこと

⚠️ ネットワークには接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.core.gates import _web_citations
from grace.tools import WebSearchTool, _unescape_json_escapes

ESCAPED_URL = "https://www.toshin.com/weather/detail?id\\u003d56682"
CLEAN_URL = "https://www.toshin.com/weather/detail?id=56682"
ESCAPED_TENKI = (
    "https://tenki.jp/forecast/3/16/4410/13103/10days_detail.html"
    "?code\\u003d130010\\u0026lang\\u003djp"
)
CLEAN_TENKI = (
    "https://tenki.jp/forecast/3/16/4410/13103/10days_detail.html"
    "?code=130010&lang=jp"
)


@pytest.fixture()
def tool():
    t = WebSearchTool.__new__(WebSearchTool)
    t.config = SimpleNamespace(
        web_search=SimpleNamespace(preferred_domains=[]),
    )
    t.backend = "serpapi"
    return t


def _parse(tool, item, backend="serpapi"):
    [entry] = tool._parse_to_rag_format([item], 1, backend=backend)
    return entry["payload"]


# =============================================================================
# ① ヘルパ単体
# =============================================================================

class TestUnescapeHelper:

    def test_equals_sign(self):
        assert _unescape_json_escapes(ESCAPED_URL) == CLEAN_URL

    def test_multiple_escapes(self):
        assert _unescape_json_escapes(ESCAPED_TENKI) == CLEAN_TENKI

    def test_japanese_is_not_mangled(self):
        r"""⚠️ `codecs.decode(s, "unicode_escape")` 誤用の回帰防止。

        latin-1 経由の復号だと「東京の天気」が化ける。
        """
        text = "東京の天気 — 明日は くもり（最高30℃）"

        assert _unescape_json_escapes(text) == text

    def test_japanese_survives_alongside_an_escape(self):
        assert _unescape_json_escapes("東京\\u003d天気") == "東京=天気"

    def test_escaped_japanese_is_restored(self):
        assert _unescape_json_escapes("\\u6771\\u4eac") == "東京"

    def test_no_escape_returns_the_same_object(self):
        text = "https://example.com/a?b=c"

        assert _unescape_json_escapes(text) is text

    def test_empty_and_falsy(self):
        assert _unescape_json_escapes("") == ""
        assert _unescape_json_escapes(None) is None

    def test_lone_surrogate_is_left_alone(self):
        r"""単独サロゲートは不正な文字になり後段のエンコードを壊すので残す。"""
        text = "x\\ud800y"

        assert _unescape_json_escapes(text) == text
        text.encode("utf-8")  # 例外が出なければよい

    def test_incomplete_escape_is_untouched(self):
        r"""16 進 4 桁に足りない・16 進でないものは触らない。"""
        for text in ("\\u00", "\\uZZZZ", "\\unicode"):
            assert _unescape_json_escapes(text) == text

    def test_uppercase_hex(self):
        assert _unescape_json_escapes("a\\u003Db") == "a=b"


# =============================================================================
# ② 変換経路（バックエンド別）
# =============================================================================

class TestParseToRagFormat:

    def test_serpapi_link(self, tool):
        payload = _parse(tool, {"link": ESCAPED_URL, "title": "t", "snippet": "s"})

        assert payload["source"] == CLEAN_URL

    def test_duckduckgo_href(self, tool):
        payload = _parse(
            tool, {"href": ESCAPED_TENKI, "title": "t", "body": "s"},
            backend="duckduckgo",
        )

        assert payload["source"] == CLEAN_TENKI

    def test_google_cse_uses_the_serpapi_shape(self, tool):
        payload = _parse(
            tool, {"link": ESCAPED_URL, "title": "t", "snippet": "s"},
            backend="google_cse",
        )

        assert payload["source"] == CLEAN_URL

    def test_title_and_snippet_are_normalized(self, tool):
        payload = _parse(tool, {
            "link": CLEAN_URL,
            "title": "東洋経済\\u003d天気",
            "snippet": "最高気温 30\\u2103",
        })

        assert payload["title"] == "東洋経済=天気"
        assert payload["answer"] == "最高気温 30℃"

    def test_japanese_payload_is_intact(self, tool):
        payload = _parse(tool, {
            "link": CLEAN_URL,
            "title": "東京の天気 - tenki.jp",
            "snippet": "明日はくもり。最高30℃／最低22℃。",
        })

        assert payload["title"] == "東京の天気 - tenki.jp"
        assert payload["answer"] == "明日はくもり。最高30℃／最低22℃。"

    def test_missing_fields_do_not_raise(self, tool):
        payload = _parse(tool, {})

        assert payload["source"] == ""
        assert payload["title"] == ""


# =============================================================================
# ③ 引用表示まで届く
# =============================================================================

class TestCitationsAreClean:

    def test_web_citation_shows_a_usable_url(self, tool):
        entries = tool._parse_to_rag_format(
            [{"link": ESCAPED_URL, "title": "東京の天気", "snippet": "くもり"}],
            1, backend="serpapi",
        )

        [citation] = _web_citations(entries)

        assert citation == f"[Web] 東京の天気（{CLEAN_URL}）"
        assert "\\u003d" not in citation
