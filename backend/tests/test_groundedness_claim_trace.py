# backend/tests/test_groundedness_claim_trace.py
"""**どの主張が矛盾と判定されたか**を追えることを固定するテスト。

## 背景（実測）

「明日の東京の天気は？」の実行ログには、こうとしか出ていなかった:

    [groundedness] supported=2 / total=3 / contradiction=True / verified=True
    Groundedness contradiction detected (supported=2, contradicted=1);
      capping answer_conf at 0.300

矛盾 1 件で `answer_conf` が 0.30 に cap され、最終信頼度は 0.378 まで落ちた。
ところが **どの主張が矛盾と判定されたのかがどこにも出ていない**ため、

  - 回答が本当に情報源と矛盾していた（正しい検知）のか
  - 検証器の誤検知で、正しい回答の信頼度を不当に下げたのか

を後から切り分けられなかった。このときの回答は「その他の情報源からは明日の
東京の具体的な天気予報を直接的に確認することはできませんでした」で、情報源に
「東京の今日・明日の天気」等の見出しがあるため**表層一致による誤検知の疑い**が
あったが、確かめる材料が無かった。

ここで固定すること:
  1. `GroundednessVerifier` が主張ごとの判定を捨てず `claims` に残すこと
  2. 矛盾判定は**本文つきで**ログに出ること（件数だけにしない）
  3. `_contradicted_claims()` が矛盾主張だけを取り出すこと（表示用の上限つき）
  4. ③ のステップイベントに矛盾主張が載ること（UI から読めること）

⚠️ LLM には接続しない（`client.models.generate_content` を差し替える）。
"""
from __future__ import annotations

import json
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.core.gates import _contradicted_claims
from backend.app.core.support_agent import (
    AUTO_PROCEED,
    SupportEvent,
    run_support_agent_core,
)
from grace.confidence import GroundednessVerifier

QUERY = "明日の東京の天気は？"
ANSWER = "その他の情報源からは、明日の東京の具体的な天気予報を確認できませんでした。"
SOURCES = ["A: 東京の今日・明日の天気。最高気温は 31℃", "A: 東京都の天気予報"]

# 実測で矛盾と判定された疑いのある主張（誤検知の検証にはこの本文が要る）
CONTRADICTED_CLAIM = "その他の情報源からは明日の東京の具体的な天気予報を確認できなかった"


def collect(events):
    return lambda e: events.append(e)


# =============================================================================
# ① 検証器が主張を捨てない
# =============================================================================

class TestVerifierKeepsClaims:

    def test_claims_survive_on_the_result(self):
        verifier, _ = _verifier()

        result = verifier.verify(QUERY, ANSWER, SOURCES)

        assert len(result.claims) == 3
        assert [c.verdict for c in result.claims] == [
            "supported", "supported", "contradicted",
        ]

    def test_counts_still_match_the_claims(self):
        """件数と中身が食い違わないこと（片方だけ直すと読み手が混乱する）。"""
        verifier, _ = _verifier()

        result = verifier.verify(QUERY, ANSWER, SOURCES)

        assert result.supported == sum(
            1 for c in result.claims if c.verdict == "supported"
        )
        assert result.contradicted == sum(
            1 for c in result.claims if c.verdict == "contradicted"
        )
        assert result.total == len(result.claims)

    def test_cached_result_keeps_the_claims(self):
        """2 回目（キャッシュ命中）でも中身が消えないこと。"""
        verifier, client = _verifier()

        verifier.verify(QUERY, ANSWER, SOURCES)
        second = verifier.verify(QUERY, ANSWER, SOURCES)

        assert client.models.generate_content.call_count == 1
        assert [c.verdict for c in second.claims].count("contradicted") == 1


# =============================================================================
# ② 矛盾は本文つきでログに出る
# =============================================================================

class TestContradictionIsLogged:

    def test_contradicted_claim_text_is_logged(self, caplog):
        verifier, _ = _verifier()

        with caplog.at_level("WARNING", logger="grace.confidence"):
            verifier.verify(QUERY, ANSWER, SOURCES)

        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "contradicted" in logged
        assert CONTRADICTED_CLAIM in logged, (
            "矛盾と判定された主張の本文が出ないと、誤検知かどうか判断できない"
        )

    def test_breakdown_lists_every_verdict(self, caplog):
        verifier, _ = _verifier()

        with caplog.at_level("INFO", logger="grace.confidence"):
            verifier.verify(QUERY, ANSWER, SOURCES)

        breakdown = [r.getMessage() for r in caplog.records if "判定内訳" in r.getMessage()]
        assert len(breakdown) == 1
        assert breakdown[0].count("supported") >= 2
        assert "contradicted" in breakdown[0]

    def test_no_contradiction_logs_no_warning(self, caplog):
        """矛盾が無いときに警告を出さない（ログを無意味に汚さない）。"""
        verifier, client = _verifier()
        client.models.generate_content.return_value = _response(
            [("主張1", "supported"), ("主張2", "neutral")]
        )

        with caplog.at_level("WARNING", logger="grace.confidence"):
            verifier.verify(QUERY, "別の回答", SOURCES)

        assert not [r for r in caplog.records if "contradicted" in r.getMessage()]


# =============================================================================
# ③ _contradicted_claims（表示用の取り出し）
# =============================================================================

class TestContradictedClaimsHelper:

    def test_picks_only_contradicted(self):
        gres = _result([
            ("支持された主張", "supported"),
            ("矛盾した主張", "contradicted"),
            ("無関係な主張", "neutral"),
        ])

        assert _contradicted_claims(gres) == ["矛盾した主張"]

    def test_tolerates_a_result_without_claims(self):
        """claims を持たない結果（旧経路・スタブ）でも落ちない。"""
        assert _contradicted_claims(SimpleNamespace(contradicted=1)) == []
        assert _contradicted_claims(SimpleNamespace(claims=None)) == []

    def test_long_claim_is_truncated(self):
        gres = _result([("あ" * 300, "contradicted")])

        [text] = _contradicted_claims(gres, max_chars=50)

        assert len(text) == 51        # 50 文字 + 省略記号
        assert text.endswith("…")

    def test_respects_the_limit(self):
        gres = _result([(f"矛盾{i}", "contradicted") for i in range(10)])

        assert len(_contradicted_claims(gres, limit=3)) == 3

    def test_whitespace_is_flattened(self):
        """改行入りの主張でもログ 1 行に収まること。"""
        gres = _result([("前半\n\n  後半", "contradicted")])

        assert _contradicted_claims(gres) == ["前半 後半"]

    def test_empty_claim_text_is_skipped(self):
        gres = _result([("", "contradicted"), ("   ", "contradicted")])

        assert _contradicted_claims(gres) == []


# =============================================================================
# ④ ③ ステップイベントから読める
# =============================================================================

class TestConfidenceStepCarriesClaims:

    def _confidence_event(self, events):
        return [e for e in events if e.type == "step"
                and e.step == "confidence" and e.status == "finished"][0]

    def test_event_carries_contradicted_claims(self, pipeline_stub):
        pipeline_stub.groundedness.contradicted = 1
        pipeline_stub.groundedness.has_contradiction = True
        pipeline_stub.groundedness.claims = [
            SimpleNamespace(claim=CONTRADICTED_CLAIM, verdict="contradicted"),
            SimpleNamespace(claim="支持された主張", verdict="supported"),
        ]
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        data = self._confidence_event(events).data
        assert data["contradicted_claims"] == [CONTRADICTED_CLAIM]

    def test_claim_appears_in_the_step_log(self, pipeline_stub):
        pipeline_stub.groundedness.contradicted = 1
        pipeline_stub.groundedness.has_contradiction = True
        pipeline_stub.groundedness.claims = [
            SimpleNamespace(claim=CONTRADICTED_CLAIM, verdict="contradicted"),
        ]
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        logs = [e.message for e in events if e.type == "log" and e.step == "confidence"]
        assert any(CONTRADICTED_CLAIM in m for m in logs)

    def test_no_contradiction_emits_an_empty_list(self, pipeline_stub):
        """矛盾なしの既定シナリオでログ・イベントが増えないこと。"""
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        data = self._confidence_event(events).data
        assert data["contradicted_claims"] == []
        logs = [e.message for e in events if e.type == "log" and e.step == "confidence"]
        assert not [m for m in logs if "矛盾と判定された主張" in m]


# =============================================================================
# ヘルパ
# =============================================================================

def _response(claims):
    response = MagicMock()
    response.text = json.dumps(
        {"claims": [{"claim": c, "verdict": v} for c, v in claims], "reason": "テスト"},
        ensure_ascii=False,
    )
    return response


def _result(claims):
    """`_contradicted_claims` に渡す最小の結果オブジェクト。"""
    return SimpleNamespace(
        claims=[SimpleNamespace(claim=c, verdict=v) for c, v in claims]
    )


def _verifier():
    """LLM に触れない `GroundednessVerifier` を作る（実測の 2 支持 1 矛盾）。"""
    verifier = GroundednessVerifier.__new__(GroundednessVerifier)
    verifier.config = None
    verifier.model_name = "test-model"
    verifier._cache = OrderedDict()

    client = MagicMock()
    client.models.generate_content.return_value = _response([
        ("Yahoo!天気で今日・明日の天気を見られる", "supported"),
        ("東京都の天気予報を地図上で見られる", "supported"),
        (CONTRADICTED_CLAIM, "contradicted"),
    ])
    verifier.client = client
    return verifier, client


@pytest.fixture(autouse=True)
def _no_real_config(monkeypatch):
    """`heavy_thinking_budget(self.config)` が None config でも動くようにする。"""
    monkeypatch.setattr("grace.confidence.heavy_thinking_budget", lambda _cfg: 0)
