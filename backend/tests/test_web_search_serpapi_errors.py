# backend/tests/test_web_search_serpapi_errors.py
"""SerpAPI が失敗したときのログと例外（`grace/tools.py`）。

実測 2026-08-29（grace_v2 / 住民票＋天気）: SerpAPI が 3 回連続で HTTP 500 を
返したが、ログに残ったのはステータス行だけで**理由が一切分からなかった**。
同じログに **API キーが平文で出力されていた**（requests の例外メッセージが
リクエスト URL を含み、SerpAPI はキーをクエリパラメータで受け取るため）。

このテストは次の 2 点を固定する:

1. 失敗時は**応答本文**（SerpAPI の `{"error": ...}`）をログに残す
2. ログにも例外にも **API キーを出さない**
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from grace.tools import WebSearchTool, _mask_secret

API_KEY = "test-secret-key-12345"
ERROR_BODY = '{"error": "Google hasn\'t returned any results for this query."}'


class _Resp:
    def __init__(self, status: int, text: str):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            # requests と同じく、メッセージに URL（＝キー）を含める
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Server Error for url: "
                f"https://serpapi.com/search.json?api_key={API_KEY}&q=x",
                response=self,
            )

    def json(self):
        return {}


def _tool(monkeypatch, status: int, calls: list):
    def _get(url, params=None, timeout=None):
        calls.append(params)
        return _Resp(status, ERROR_BODY)

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    monkeypatch.setenv("SERPAPI_KEY", API_KEY)

    tool = WebSearchTool.__new__(WebSearchTool)      # __init__ を通さず最小構成
    tool.config = SimpleNamespace(
        web_search=SimpleNamespace(serpapi_api_key=API_KEY)
    )
    tool.timeout = 1
    tool.max_retries = 3
    tool.retry_backoff = 0.0
    return tool


class TestMaskSecret:
    def test_キーを伏せる(self):
        got = _mask_secret(f"url?api_key={API_KEY}&q=x", API_KEY)
        assert API_KEY not in got
        assert "***" in got

    def test_キーが空なら素通し(self):
        assert _mask_secret("text", "") == "text"


class TestSerpapiErrorDiagnostics:
    def test_500の応答本文をログに残す(self, monkeypatch, caplog):
        """⚠️ これが無いと「500 Server Error」しか残らず原因を追えない。"""
        calls: list = []
        tool = _tool(monkeypatch, 500, calls)
        with caplog.at_level("ERROR"), pytest.raises(requests.exceptions.HTTPError):
            tool._search_serpapi("住民票の写しの取り方は？", 5, "ja")
        assert any("Google hasn't returned any results" in r.message
                   for r in caplog.records), "SerpAPI の error 本文がログに無い"

    def test_ログにAPIキーが出ない(self, monkeypatch, caplog):
        calls: list = []
        tool = _tool(monkeypatch, 500, calls)
        with caplog.at_level("DEBUG"), pytest.raises(requests.exceptions.HTTPError):
            tool._search_serpapi("q", 5, "ja")
        for record in caplog.records:
            assert API_KEY not in record.getMessage()

    def test_送出する例外にAPIキーが出ない(self, monkeypatch):
        """上位が `logger.error(f\"...: {e}\", exc_info=True)` で出しても漏れない。"""
        calls: list = []
        tool = _tool(monkeypatch, 500, calls)
        with pytest.raises(requests.exceptions.HTTPError) as excinfo:
            tool._search_serpapi("q", 5, "ja")
        assert API_KEY not in str(excinfo.value)
        assert "***" in str(excinfo.value)

    def test_5xxは設定回数だけ再試行する(self, monkeypatch):
        calls: list = []
        tool = _tool(monkeypatch, 500, calls)
        with pytest.raises(requests.exceptions.HTTPError):
            tool._search_serpapi("q", 5, "ja")
        assert len(calls) == 3

    def test_4xxは再試行しない(self, monkeypatch):
        """キー不正・クォータ超過は再試行しても解消しない。"""
        calls: list = []
        tool = _tool(monkeypatch, 401, calls)
        with pytest.raises(requests.exceptions.HTTPError):
            tool._search_serpapi("q", 5, "ja")
        assert len(calls) == 1
