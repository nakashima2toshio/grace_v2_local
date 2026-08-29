# backend/tests/test_web_search_ddg_backend.py
"""DDG フォールバックのバックエンド選択（`grace/tools.py::_search_ddg`）。

実測 2026-08-29（grace_v2 / 住民票＋天気）: SerpAPI が 500 で落ちたあと
フォールバックの DuckDuckGo も **0 件**だった。検索先からは HTTP 200 が
返っており、旧パッケージ `duckduckgo_search`（8.1.1 で更新停止）が
現在の HTML を解析できていないだけだった。

⚠️ **受け皿が動いていないことは、0 件と区別がつかない。**
0 件は下流で「情報なし回答」→ 誤エスカレにつながるため、
どのパッケージで 0 件になったのかをログに残す。
"""
from __future__ import annotations

import sys
from types import ModuleType

from grace.tools import WebSearchTool

HIT = {"title": "住民票の写し", "href": "https://example.lg.jp/juminhyo",
       "body": "窓口・郵送・コンビニ交付で取得できます。"}


def _fake_ddgs_module(results: list) -> ModuleType:
    """`ddgs` / `duckduckgo_search` の代役。text() の呼び出しも記録する。"""
    calls: list = []

    class _DDGS:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def text(self, query, region=None, max_results=None):
            calls.append({"query": query, "region": region,
                          "max_results": max_results})
            return results

    module = ModuleType("stub")
    module.DDGS = _DDGS
    module.calls = calls
    return module


def _tool():
    tool = WebSearchTool.__new__(WebSearchTool)   # __init__ を通さず最小構成
    tool.timeout = 5
    return tool


class TestDdgBackend:
    def test_新パッケージddgsを優先する(self, monkeypatch):
        """⚠️ 旧名が入っている環境でも、新しい方を使う。"""
        new = _fake_ddgs_module([HIT])
        old = _fake_ddgs_module([])
        monkeypatch.setitem(sys.modules, "ddgs", new)
        monkeypatch.setitem(sys.modules, "duckduckgo_search", old)

        assert _tool()._search_ddg("住民票の写しの取り方", 5, "ja") == [HIT]
        assert len(new.calls) == 1
        assert not old.calls, "旧パッケージを呼んではいけない"

    def test_ddgsが無ければ旧パッケージへ落ちる(self, monkeypatch):
        """pyproject を反映していない venv でも動かす（保険）。"""
        old = _fake_ddgs_module([HIT])
        monkeypatch.setitem(sys.modules, "ddgs", None)   # import で ImportError
        monkeypatch.setitem(sys.modules, "duckduckgo_search", old)

        assert _tool()._search_ddg("q", 5, "ja") == [HIT]
        assert len(old.calls) == 1

    def test_日本語はjpリージョンで引く(self, monkeypatch):
        new = _fake_ddgs_module([HIT])
        monkeypatch.setitem(sys.modules, "ddgs", new)
        _tool()._search_ddg("住民票", 3, "ja")
        assert new.calls[0] == {"query": "住民票", "region": "jp-jp",
                                "max_results": 3}

    def test_0件はwarningでパッケージ名を残す(self, monkeypatch, caplog):
        """0 件が『見つからない』のか『解析できていない』のか追えるようにする。"""
        new = _fake_ddgs_module([])
        monkeypatch.setitem(sys.modules, "ddgs", new)
        with caplog.at_level("WARNING"):
            assert _tool()._search_ddg("q", 5, "ja") == []
        assert any("0 results" in r.getMessage() and "ddgs" in r.getMessage()
                   for r in caplog.records)
