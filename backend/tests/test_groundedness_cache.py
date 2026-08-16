# backend/tests/test_groundedness_cache.py
"""同じ回答・同じソースを **2 回検証しない** ことを固定するテスト。

## 背景（実測）

「明日の東京の天気は？」1 リクエストのログ:

    20:12:26→20:12:53  executor._blend_groundedness_confidence  27.3 秒
    20:12:53→20:13:13  support_agent ③ 根拠評価                 19.9 秒

入力（query / answer / 14 件のソース）は完全に同一で、判定も同じ 6/6。
温度 0 なので 2 回目に新しい情報は無く、**47 秒＝リクエスト全体 2:00 の 39%**
がまるごと待ち時間だった。起動ログにも `GroundednessVerifier initialized` が
2 行出ており、別インスタンスが 2 つ立っていたのが原因。

ここでは 2 つを固定する:
  1. `GroundednessVerifier` が同一入力をメモし、LLM を 1 回しか呼ばないこと
  2. 入力が違えば従来どおり検証すること（⑤ の Web 回答検証を壊さない）

⚠️ LLM には接続しない（`client.models.generate_content` を差し替える）。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from grace.confidence import GroundednessVerifier

QUERY = "明日の東京の天気は？"
ANSWER = "明日（8/17）の東京は雨のち晴れの見込みです。"
SOURCES = ["A: 東京は雨のち晴れ", "A: 最高気温は 26℃"]


# =============================================================================
# ① 同一入力は 1 回だけ検証する
# =============================================================================

class TestCacheHit:

    def test_second_call_does_not_hit_the_llm(self):
        verifier, client = _verifier(supported=6, total=6)

        first = verifier.verify(QUERY, ANSWER, SOURCES)
        second = verifier.verify(QUERY, ANSWER, SOURCES)

        assert client.models.generate_content.call_count == 1, (
            "同一入力で LLM を 2 回呼んでいる（実測では 47 秒の無駄になった）"
        )
        assert second == first

    def test_repeated_calls_stay_at_one(self):
        verifier, client = _verifier(supported=3, total=3)
        for _ in range(5):
            verifier.verify(QUERY, ANSWER, SOURCES)

        assert client.models.generate_content.call_count == 1

    def test_cache_hit_is_logged(self, caplog):
        verifier, _ = _verifier(supported=6, total=6)
        verifier.verify(QUERY, ANSWER, SOURCES)

        with caplog.at_level("INFO", logger="grace.confidence"):
            verifier.verify(QUERY, ANSWER, SOURCES)

        assert any("cache hit" in r.message for r in caplog.records), (
            "再利用したことがログに出ないと、二重実行が直ったか確認できない"
        )

    def test_result_values_are_preserved(self):
        verifier, _ = _verifier(supported=6, total=6)

        first = verifier.verify(QUERY, ANSWER, SOURCES)
        second = verifier.verify(QUERY, ANSWER, SOURCES)

        assert second.support_rate == first.support_rate == 1.0
        assert second.supported == 6
        assert second.verified is True


# =============================================================================
# ② 入力が違えば検証しなおす
# =============================================================================

class TestCacheMiss:

    def test_different_answer_is_verified_again(self):
        """⑤ の Web フォールバックは別の回答を検証する。ここを潰してはいけない。"""
        verifier, client = _verifier(supported=2, total=2)

        verifier.verify(QUERY, ANSWER, SOURCES)
        verifier.verify(QUERY, "別の回答です。", SOURCES)

        assert client.models.generate_content.call_count == 2

    def test_different_sources_are_verified_again(self):
        verifier, client = _verifier(supported=2, total=2)

        verifier.verify(QUERY, ANSWER, SOURCES)
        verifier.verify(QUERY, ANSWER, SOURCES + ["A: 追加の情報源"])

        assert client.models.generate_content.call_count == 2

    def test_source_order_matters(self):
        """並び順が違えばプロンプトも違う。同一視しない（安全側）。"""
        verifier, client = _verifier(supported=2, total=2)

        verifier.verify(QUERY, ANSWER, SOURCES)
        verifier.verify(QUERY, ANSWER, list(reversed(SOURCES)))

        assert client.models.generate_content.call_count == 2

    def test_different_query_is_verified_again(self):
        verifier, client = _verifier(supported=2, total=2)

        verifier.verify(QUERY, ANSWER, SOURCES)
        verifier.verify("別の質問は？", ANSWER, SOURCES)

        assert client.models.generate_content.call_count == 2


# =============================================================================
# ③ 失敗は覚えない
# =============================================================================

class TestFailuresAreNotCached:

    def test_exception_is_retried_on_the_next_call(self):
        """タイムアウトは入力ではなく実行時の事情。次は成功しうる。

        失敗を覚えると 1 回の瞬断で後続の全経路が「検証不能」に固定され、
        `_should_rescue_unverified` の救済判断まで巻き添えになる。
        """
        verifier, client = _verifier(supported=6, total=6)
        client.models.generate_content.side_effect = [
            TimeoutError("Request timed out"),
            _response(supported=6, total=6),
        ]

        failed = verifier.verify(QUERY, ANSWER, SOURCES)
        assert failed.verification_failed is True

        recovered = verifier.verify(QUERY, ANSWER, SOURCES)
        assert recovered.verified is True
        assert client.models.generate_content.call_count == 2

    def test_empty_response_is_retried(self):
        """ローカル LLM の空応答（finish_reason=length）も同じ扱い。"""
        verifier, client = _verifier(supported=6, total=6)
        empty = MagicMock()
        empty.text = ""
        client.models.generate_content.side_effect = [
            empty, _response(supported=6, total=6),
        ]

        assert verifier.verify(QUERY, ANSWER, SOURCES).verification_failed is True
        assert verifier.verify(QUERY, ANSWER, SOURCES).verified is True
        assert client.models.generate_content.call_count == 2


# =============================================================================
# ④ キャッシュは無制限に伸びない
# =============================================================================

class TestCacheBound:

    def test_oldest_entry_is_evicted(self):
        verifier, client = _verifier(supported=1, total=1)

        answers = [f"回答 {i}" for i in range(verifier._CACHE_SIZE + 1)]
        for answer in answers:
            verifier.verify(QUERY, answer, SOURCES)

        calls_before = client.models.generate_content.call_count
        verifier.verify(QUERY, answers[0], SOURCES)   # 最古 → 追い出し済み
        verifier.verify(QUERY, answers[-1], SOURCES)  # 最新 → 残っている

        assert client.models.generate_content.call_count == calls_before + 1

    def test_short_circuits_are_not_cached(self):
        """空回答・ソース無しは LLM を呼ばないので、そもそも記憶しない。"""
        verifier, client = _verifier(supported=1, total=1)

        assert verifier.verify(QUERY, "", SOURCES).reason == "empty answer"
        assert verifier.verify(QUERY, ANSWER, []).reason == "no sources"
        assert client.models.generate_content.call_count == 0
        assert len(verifier._cache) == 0


# =============================================================================
# ⑤ support_agent は executor と同じ検証器を使う
# =============================================================================

class TestVerifierIsShared:
    """③ 根拠評価がキャッシュに当たるには、**同じインスタンス**である必要がある。

    別インスタンスを立てると、メモを入れても executor 側の判定は共有されず、
    実測の「27.3 秒 + 19.9 秒」がそのまま残る。
    """

    def test_uses_the_executor_verifier(self, pipeline_stub, monkeypatch):
        import backend.app.core.support_agent as sa

        executor = sa.create_executor(None, None)
        shared = _RecordingVerifier()
        executor.groundedness_verifier = shared
        monkeypatch.setattr(sa, "create_executor", lambda _c, _r: executor)
        monkeypatch.setattr(sa, "create_groundedness_verifier", _must_not_be_called)

        sa.run_support_agent_core("パスワードを忘れました", confirm=lambda _r: sa.AUTO_PROCEED)

        assert shared.calls, "executor が持つ検証器が使われていない"

    def test_falls_back_when_executor_has_none(self, pipeline_stub, monkeypatch):
        """検証器を持たない executor 実装でも動くこと（後方互換）。"""
        import backend.app.core.support_agent as sa

        executor = sa.create_executor(None, None)
        assert not hasattr(executor, "groundedness_verifier")

        result = sa.run_support_agent_core(
            "パスワードを忘れました", confirm=lambda _r: sa.AUTO_PROCEED
        )
        assert result.decision in ("answer", "escalate")
        assert pipeline_stub.verify_calls, "フォールバック側の検証器が呼ばれていない"


class _RecordingVerifier:
    def __init__(self):
        self.calls = []

    def verify(self, query, answer, sources=None):
        self.calls.append((query, answer, list(sources or [])))
        from backend.tests.conftest import GroundednessStub
        return GroundednessStub()


def _must_not_be_called(_config):
    raise AssertionError(
        "executor が検証器を持っているのに新しいインスタンスを作っている"
        "（＝同じ判定を LLM へ 2 回投げる）"
    )


# =============================================================================
# helpers
# =============================================================================

def _response(*, supported: int, total: int):
    claims = [{"claim": f"主張{i}", "verdict": "supported"} for i in range(supported)]
    claims += [{"claim": f"主張{i}", "verdict": "neutral"} for i in range(total - supported)]
    response = MagicMock()
    response.text = json.dumps({"claims": claims, "reason": "テスト"}, ensure_ascii=False)
    return response


def _verifier(*, supported: int, total: int):
    """LLM に触れない `GroundednessVerifier` を作る。"""
    verifier = GroundednessVerifier.__new__(GroundednessVerifier)
    verifier.config = None
    verifier.model_name = "test-model"
    verifier._cache = __import__("collections").OrderedDict()

    client = MagicMock()
    client.models.generate_content.return_value = _response(supported=supported, total=total)
    verifier.client = client
    return verifier, client


@pytest.fixture(autouse=True)
def _no_real_config(monkeypatch):
    """`heavy_thinking_budget(self.config)` が None config でも動くようにする。"""
    monkeypatch.setattr("grace.confidence.heavy_thinking_budget", lambda _cfg: 0)
