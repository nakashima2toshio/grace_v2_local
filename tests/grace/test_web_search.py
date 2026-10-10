# tests/grace/test_web_search.py
"""WebSearchTool のリトライ・フォールバックのテスト（ネットワーク不要・全モック）。

次工程候補②「web_search タイムアウト調整」の回帰防止:
タイムアウト等で主バックエンドが空振りすると、下流で「情報なし回答」が生成され
④' ゲートの誤エスカレにつながる（saas「500エラー報告」で顕在化）。
- SerpAPI: タイムアウト/接続エラー/5xx は設定回数までリトライ、4xx は即時失敗
- 主バックエンド失敗・0件時は fallback_backend（既定 duckduckgo）で 1 度だけ再試行
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from grace.config import GraceConfig
from grace.tools import WebSearchTool

DDG_ITEM = {"title": "DDG結果", "href": "https://example.com/ddg", "body": "DDGの本文"}
SERP_ITEM = {"title": "Serp結果", "link": "https://example.com/serp", "snippet": "Serpの要約"}


def make_tool(**web_search_overrides) -> WebSearchTool:
    config = GraceConfig()
    for key, value in web_search_overrides.items():
        setattr(config.web_search, key, value)
    return WebSearchTool(config)


class TestFallbackBackend:
    def test_fallback_on_primary_exception(self):
        """主バックエンド（serpapi）が例外 → fallback（duckduckgo）で成功"""
        tool = make_tool(backend="serpapi", fallback_backend="duckduckgo")
        with patch.object(tool, "_search_serpapi",
                          side_effect=requests.exceptions.ReadTimeout("timeout")), \
             patch.object(tool, "_search_ddg", return_value=[DDG_ITEM]):
            result = tool.execute("500エラーが発生しています")

        assert result.success is True
        assert result.confidence_factors["search_engine"] == "duckduckgo"
        # フォールバック側（DDG）の項目名 body/href で整形されること
        assert result.output[0]["payload"]["answer"] == "DDGの本文"
        assert result.output[0]["payload"]["source"] == "https://example.com/ddg"

    def test_fallback_on_primary_empty_results(self):
        """主バックエンドが 0 件 → fallback で成功"""
        tool = make_tool(backend="serpapi", fallback_backend="duckduckgo")
        with patch.object(tool, "_search_serpapi", return_value=[]), \
             patch.object(tool, "_search_ddg", return_value=[DDG_ITEM]):
            result = tool.execute("クエリ")

        assert result.success is True
        assert result.confidence_factors["search_engine"] == "duckduckgo"

    def test_no_fallback_when_disabled(self):
        """fallback_backend="" なら主バックエンドの失敗がそのまま返る"""
        tool = make_tool(backend="serpapi", fallback_backend="")
        with patch.object(tool, "_search_serpapi",
                          side_effect=requests.exceptions.ReadTimeout("timeout")) as serp, \
             patch.object(tool, "_search_ddg") as ddg:
            result = tool.execute("クエリ")

        assert result.success is False
        assert "timeout" in (result.error or "")
        serp.assert_called_once()
        ddg.assert_not_called()

    def test_primary_success_skips_fallback(self):
        """主バックエンド成功時は fallback を呼ばない"""
        tool = make_tool(backend="serpapi", fallback_backend="duckduckgo")
        with patch.object(tool, "_search_serpapi", return_value=[SERP_ITEM]), \
             patch.object(tool, "_search_ddg") as ddg:
            result = tool.execute("クエリ")

        assert result.success is True
        assert result.confidence_factors["search_engine"] == "serpapi"
        assert result.output[0]["payload"]["answer"] == "Serpの要約"
        ddg.assert_not_called()

    def test_both_backends_fail_returns_error(self):
        """主・fallback 両方失敗 → 失敗 ToolResult（例外にしない）"""
        tool = make_tool(backend="serpapi", fallback_backend="duckduckgo")
        with patch.object(tool, "_search_serpapi",
                          side_effect=requests.exceptions.ReadTimeout("t1")), \
             patch.object(tool, "_search_ddg", side_effect=RuntimeError("ddg down")):
            result = tool.execute("クエリ")

        assert result.success is False
        assert result.error


def serp_response(status_code=200, organic=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"organic_results": organic or []}
    if status_code >= 400:
        error = requests.exceptions.HTTPError(response=resp)
        resp.raise_for_status.side_effect = error
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestSerpApiRetry:
    """_search_serpapi のリトライ（timeout/5xx は再試行、4xx は即時失敗）。"""

    def setup_method(self):
        self.tool = make_tool(backend="serpapi", max_retries=3,
                              retry_backoff_seconds=0.0)

    @patch("time.sleep")
    @patch.dict("os.environ", {"SERPAPI_KEY": "test-key"})
    def test_retries_on_timeout_then_succeeds(self, _sleep):
        ok = serp_response(organic=[SERP_ITEM])
        with patch("requests.get",
                   side_effect=[requests.exceptions.ReadTimeout("t"), ok]) as get:
            results = self.tool._search_serpapi("q", 5, "ja")
        assert results == [SERP_ITEM]
        assert get.call_count == 2

    @patch("time.sleep")
    @patch.dict("os.environ", {"SERPAPI_KEY": "test-key"})
    def test_retries_on_5xx_then_succeeds(self, _sleep):
        ok = serp_response(organic=[SERP_ITEM])
        with patch("requests.get",
                   side_effect=[serp_response(status_code=500), ok]) as get:
            results = self.tool._search_serpapi("q", 5, "ja")
        assert results == [SERP_ITEM]
        assert get.call_count == 2

    @patch("time.sleep")
    @patch.dict("os.environ", {"SERPAPI_KEY": "test-key"})
    def test_4xx_fails_immediately_without_retry(self, _sleep):
        with patch("requests.get",
                   return_value=serp_response(status_code=401)) as get:
            with pytest.raises(requests.exceptions.HTTPError):
                self.tool._search_serpapi("q", 5, "ja")
        assert get.call_count == 1

    @patch("time.sleep")
    @patch.dict("os.environ", {"SERPAPI_KEY": "test-key"})
    def test_exhausted_retries_raise(self, _sleep):
        with patch("requests.get",
                   side_effect=requests.exceptions.ReadTimeout("t")) as get:
            with pytest.raises(requests.exceptions.ReadTimeout):
                self.tool._search_serpapi("q", 5, "ja")
        assert get.call_count == 3  # max_retries 回試行して送出

    @patch.dict("os.environ", {"SERPAPI_KEY": ""})
    def test_missing_api_key_raises_value_error(self):
        tool = make_tool(backend="serpapi")
        tool.config.web_search.serpapi_api_key = ""
        with pytest.raises(ValueError):
            tool._search_serpapi("q", 5, "ja")


class TestConfigDefaults:
    def test_web_search_config_defaults(self):
        config = GraceConfig()
        assert config.web_search.timeout == 30
        assert config.web_search.max_retries == 3
        assert config.web_search.retry_backoff_seconds == 2.0
        assert config.web_search.fallback_backend == "duckduckgo"
