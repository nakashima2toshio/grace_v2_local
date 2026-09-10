"""S1 GroundednessVerifier のテスト（LLM はモック）。"""

import json
from unittest.mock import MagicMock, patch

from grace.confidence import GroundednessVerifier


def _mock_client_returning(payload: dict):
    """models.generate_content が payload(JSON) を返すモッククライアント。"""
    response = MagicMock()
    response.text = json.dumps(payload, ensure_ascii=False)
    client = MagicMock()
    client.models.generate_content.return_value = response
    return client


class TestGroundednessVerifier:
    def test_no_sources_is_unverified(self):
        """ソースが無い回答は未検証（verified=False）"""
        with patch("grace.confidence.create_chat_client", return_value=MagicMock()):
            verifier = GroundednessVerifier()
        result = verifier.verify("質問", "回答", sources=[])
        assert result.verified is False
        assert result.support_rate == 0.0

    def test_all_supported(self):
        """全主張が supported → 支持率 1.0"""
        payload = {
            "claims": [
                {"claim": "A", "verdict": "supported"},
                {"claim": "B", "verdict": "supported"},
            ],
            "reason": "ok",
        }
        with patch("grace.confidence.create_chat_client",
                   return_value=_mock_client_returning(payload)):
            verifier = GroundednessVerifier()
            result = verifier.verify("質問", "回答", sources=["出典1"])
        assert result.verified is True
        assert result.supported == 2
        assert result.contradicted == 0
        assert result.support_rate == 1.0
        assert result.has_contradiction is False

    def test_contradiction_detected(self):
        """矛盾が含まれると has_contradiction=True、支持率は decided ベース"""
        payload = {
            "claims": [
                {"claim": "A", "verdict": "supported"},
                {"claim": "B", "verdict": "contradicted"},
                {"claim": "C", "verdict": "neutral"},
            ],
            "reason": "一部矛盾",
        }
        with patch("grace.confidence.create_chat_client",
                   return_value=_mock_client_returning(payload)):
            verifier = GroundednessVerifier()
            result = verifier.verify("質問", "回答", sources=["出典1"])
        assert result.has_contradiction is True
        assert result.supported == 1
        assert result.contradicted == 1
        assert result.total == 3
        # decided = supported + contradicted = 2 → support_rate = 1/2
        assert abs(result.support_rate - 0.5) < 1e-9

    def test_llm_failure_is_graceful(self):
        """LLM 呼び出し例外時は未検証で評価を止めない"""
        client = MagicMock()
        client.models.generate_content.side_effect = Exception("API down")
        with patch("grace.confidence.create_chat_client", return_value=client):
            verifier = GroundednessVerifier()
            result = verifier.verify("質問", "回答", sources=["出典1"])
        assert result.verified is False
        assert result.support_rate == 0.0
        assert "error" in result.reason
