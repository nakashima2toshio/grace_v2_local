# backend/tests/test_verification_failure.py
"""groundedness 検証器の**失敗**を「支持できなかった」と同一視しないテスト。

## 何を守っているのか

`GroundednessVerifier.verify()` は例外・タイムアウト・空応答のとき
`verified=False` を返す。`_answer_gate` はこれを一律 escalate にするため、
**生成に成功した回答まで捨てて escalate する**壊れ方をしていた。

実測（ローカル Ollama, gemma4:26b-a4b-it-qat）:

    16:07:10  reasoning 成功（107 文字・内容も正しい）
    16:08:43  Retrying request ...        ← 検証 LLM がタイムアウト
    16:11:43  Reasoning failed: Request timed out
              → verified=False → escalate → answer=internal_answer（空）

答えを作れているのに答えない、という一番まずい壊れ方。原因は
`verified=False` が性質の違う 2 つを混ぜていたこと:

  (a) 検証は動いたが主張を肯定できなかった → escalate で正しい
  (b) 検証器そのものが判定できなかった     → 回答の質とは無関係

ここでは (b) を `verification_failed=True` で区別し、矛盾なし・出典ありの
ときだけ未確認注記つきの answer として残すことを固定する。

⚠️ 実際の LLM / Ollama サーバへは接続しない。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.app.core.gates import _answer_gate, _should_rescue_unverified
from grace.confidence import GroundednessResult, GroundednessVerifier

# =============================================================================
# ① GroundednessResult が「判定できなかった」と「肯定できなかった」を区別する
# =============================================================================

class TestVerificationFailedFlag:

    def test_default_is_false(self):
        """既存の呼び出し（位置引数 7 個）を壊さないこと。"""
        res = GroundednessResult(0.5, 1, 1, 2, False, True, "ok")
        assert res.verification_failed is False

    def test_exception_marks_verification_failed(self):
        """検証 LLM が例外を投げたら verification_failed=True。"""
        verifier = _verifier_raising(TimeoutError("Request timed out"))
        res = verifier.verify("Q", "A", ["source text"])

        assert res.verified is False
        assert res.verification_failed is True
        assert "error:" in res.reason

    def test_empty_response_marks_verification_failed(self):
        """本文 0 文字（finish_reason=length）も判定不能として扱う。"""
        verifier = _verifier_returning(_response(text=""))
        res = verifier.verify("Q", "A", ["source text"])

        assert res.verified is False
        assert res.verification_failed is True
        assert res.reason == "empty response"

    @pytest.mark.parametrize(
        "answer, sources",
        [("", ["source text"]), ("A", []), ("A", None)],
    )
    def test_missing_input_is_not_a_verification_failure(self, answer, sources):
        """回答やソースが無いのは**検証器の失敗ではない**（従来どおり escalate）。"""
        verifier = _verifier_returning(_response(text="{}"))
        res = verifier.verify("Q", answer, sources)

        assert res.verified is False
        assert res.verification_failed is False


# =============================================================================
# ② 検証器の失敗だけを理由に回答を捨てない
# =============================================================================

class TestRescueUnverified:

    def test_rescues_when_verifier_failed(self):
        """矛盾なし・出典あり・回答ありなら救済する。"""
        assert _should_rescue_unverified(
            decision="escalate", verification_failed=True,
            has_contradiction=False, citation_count=3,
            answer="住民票の写しは窓口・郵送・コンビニで取得できます。",
        ) is True

    def test_does_not_rescue_when_verifier_worked(self):
        """(a) 判定はできたが肯定できなかった場合は従来どおり escalate。"""
        assert _should_rescue_unverified(
            decision="escalate", verification_failed=False,
            has_contradiction=False, citation_count=3, answer="回答本文",
        ) is False

    def test_does_not_rescue_on_contradiction(self):
        """矛盾ありは安全側に倒す。"""
        assert _should_rescue_unverified(
            decision="escalate", verification_failed=True,
            has_contradiction=True, citation_count=3, answer="回答本文",
        ) is False

    def test_does_not_rescue_without_citations(self):
        """出典 0 件の回答は根拠を示せないので救済しない。"""
        assert _should_rescue_unverified(
            decision="escalate", verification_failed=True,
            has_contradiction=False, citation_count=0, answer="回答本文",
        ) is False

    def test_does_not_rescue_empty_answer(self):
        """そもそも救う回答が無い。"""
        assert _should_rescue_unverified(
            decision="escalate", verification_failed=True,
            has_contradiction=False, citation_count=3, answer="",
        ) is False

    def test_does_not_touch_answer_decision(self):
        """既に answer なら救済は不要（判定を変えない）。"""
        assert _should_rescue_unverified(
            decision="answer", verification_failed=True,
            has_contradiction=False, citation_count=3, answer="回答本文",
        ) is False


# =============================================================================
# ③ 実測シナリオ: 検証タイムアウトで回答が消えない
# =============================================================================

class TestRealWorldScenario:
    """16:07:10 の 107 文字回答が 16:11:43 の検証タイムアウトで消えた件。"""

    ANSWER = "住民票の写しは市区町村の窓口、郵送、またはコンビニ交付で取得できます。"

    def test_gate_alone_would_discard_the_answer(self):
        """救済が無いと escalate になる（＝この救済が必要な理由）。"""
        gres = GroundednessResult(
            0.0, 0, 0, 0, False, False, "error: Request timed out",
            verification_failed=True,
        )
        decision, _ = _answer_gate(
            gres.support_rate, gres.verified, 9, notify_th=0.7, confirm_th=0.4,
        )
        assert decision == "escalate"

    def test_rescue_keeps_the_answer_with_warning(self):
        """救済後は未確認注記つきの answer になる。"""
        gres = GroundednessResult(
            0.0, 0, 0, 0, False, False, "error: Request timed out",
            verification_failed=True,
        )
        decision, warning = _answer_gate(
            gres.support_rate, gres.verified, 9, notify_th=0.7, confirm_th=0.4,
        )
        if _should_rescue_unverified(
            decision, gres.verification_failed, gres.has_contradiction,
            9, self.ANSWER,
        ):
            decision, warning = "answer", True

        assert decision == "answer"
        assert warning is True, "救済した回答には必ず未確認の注記を付ける"

    def test_low_support_rate_still_escalates(self):
        """検証が**動いた**うえでの低支持率は救済されない（安全側の維持）。"""
        gres = GroundednessResult(
            0.1, 1, 9, 10, True, True, "mostly contradicted",
        )
        decision, _ = _answer_gate(
            gres.support_rate, gres.verified, 9, notify_th=0.7, confirm_th=0.4,
        )
        assert decision == "escalate"
        assert _should_rescue_unverified(
            decision, gres.verification_failed, gres.has_contradiction,
            9, self.ANSWER,
        ) is False


# =============================================================================
# helpers
# =============================================================================

def _response(text: str) -> MagicMock:
    res = MagicMock()
    res.text = text
    return res


def _build_verifier() -> GroundednessVerifier:
    """実クライアントを作らずに verifier を組み立てる。"""
    with patch("grace.confidence.create_chat_client", return_value=MagicMock()):
        return GroundednessVerifier()


def _verifier_raising(exc: Exception) -> GroundednessVerifier:
    verifier = _build_verifier()
    verifier.client.models.generate_content.side_effect = exc
    return verifier


def _verifier_returning(response: MagicMock) -> GroundednessVerifier:
    verifier = _build_verifier()
    verifier.client.models.generate_content.return_value = response
    return verifier
