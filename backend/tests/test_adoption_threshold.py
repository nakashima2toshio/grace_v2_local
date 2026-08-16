# backend/tests/test_adoption_threshold.py
"""採用閾値 `executor.reasoning_min_rag_score` を **実測値に固定する** テスト。

## 何を守っているのか

この値は「reasoning に渡す下限」と「RAG 検索の採用下限」を兼ねる。

    不変条件: 推論に使えない文書は、出典としても採用しない。

値そのものは推測ではなく実測から決めた（`scripts/measure_rag_threshold.py`、
汎用コーパスを除外した業務コレクション 6 件が対象）。

| | n | 最小 | 中央 | 最大 |
|---|--:|--:|--:|--:|
| in_scope（拾いたい） | 12 | **0.6650** | 0.7714 | 0.8253 |
| out_scope（拾いたくない） | 5 | 0.5615 | 0.6004 | **0.6190** |

    TP フロア    0.6650  ← これ未満にすると業務質問を取りこぼす
    FP シーリング 0.6190  ← これ以下にすると範囲外質問を誤採用する
    → 中間の 0.64

⚠️ **除外を入れる前は分離できなかった**（FP 0.7054 > TP 0.6650）。
スコープを直したからこの値が決められた。順序が逆だと、どこに置いても
取りこぼすか誤採用するかになる。

⚠️ **マージンは 0.046 しかない。** サンプルが少ないので暫定値である。
ここでは「実測区間の内側にあること」を守り、特定の数値そのものは固定しない
（測り直して動かせる余地を残す）。ただし区間から外れたら落とす。

⚠️ Qdrant にも LLM にも接続しない。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from grace.config import ExecutorConfig, GraceConfig
from grace.tools import RAGSearchTool

# 実測値（scripts/measure_rag_threshold.py --vertical all、除外あり）
TP_FLOOR = 0.6650      # in_scope の最小 = 「SSO の設定手順は？」
FP_CEILING = 0.6190    # out_scope の最大 = 「近くのおいしいラーメン屋を教えて」

MEASURED_IN_SCOPE = {
    "領収書は発行できますか？": 0.8253,
    "住民票の写しの取り方は？": 0.8011,
    "API のレート制限はどれくらいですか？": 0.7968,
    "注文のキャンセルはどうすればいいですか？": 0.7836,
    "送料が無料になる条件は？": 0.7755,
    "国民健康保険の加入手続きはどこでできますか？": 0.7740,
    "印鑑登録に必要な持ち物を教えてください": 0.7687,
    "返品はいつまで受け付けていますか？": 0.7556,
    "転入届の提出期限はいつまでですか？": 0.7355,
    "Webhook の再送仕様を教えてください": 0.7147,
    "アクセストークンの有効期限は？": 0.6763,
    "SSO の設定手順は？": 0.6650,
}
MEASURED_OUT_OF_SCOPE = {
    "近くのおいしいラーメン屋を教えて": 0.6190,
    "明日の東京の天気は？": 0.6009,
    "今日の日経平均株価はいくらですか？": 0.6004,
    "円ドル相場の見通しは？": 0.5948,
    "今年のノーベル物理学賞は誰ですか？": 0.5615,
}


# =============================================================================
# ① 閾値は実測区間の内側にある
# =============================================================================

class TestThresholdIsWithinTheMeasuredGap:

    def test_does_not_drop_any_in_scope_query(self):
        """業務質問を 1 件も取りこぼさないこと。"""
        threshold = ExecutorConfig().reasoning_min_rag_score
        dropped = {q: s for q, s in MEASURED_IN_SCOPE.items() if s < threshold}

        assert not dropped, (
            f"閾値 {threshold} は業務質問を取りこぼす: {dropped}"
            f"（TP フロア {TP_FLOOR} 未満に置いてはいけない）"
        )

    def test_rejects_every_out_of_scope_query(self):
        """範囲外質問を 1 件も採用しないこと。"""
        threshold = ExecutorConfig().reasoning_min_rag_score
        adopted = {q: s for q, s in MEASURED_OUT_OF_SCOPE.items() if s >= threshold}

        assert not adopted, (
            f"閾値 {threshold} は範囲外質問を誤採用する: {adopted}"
            f"（FP シーリング {FP_CEILING} 以下に置いてはいけない）"
        )

    def test_yaml_and_class_default_agree(self):
        """`config/grace_config.yml` とクラス既定が食い違っていないこと。

        yml は「既定値を明示して見えるようにする」方針なので、ずれると
        ドキュメントとして機能しなくなる。
        """
        assert (
            GraceConfig().executor.reasoning_min_rag_score
            == ExecutorConfig().reasoning_min_rag_score
        )

    def test_the_gap_is_narrow_and_that_is_recorded(self):
        """余裕が狭いこと自体を記録しておく（暫定値である根拠）。"""
        assert TP_FLOOR - FP_CEILING == pytest.approx(0.046, abs=0.001)


# =============================================================================
# ② 採用側にも同じ値が効く
# =============================================================================

class TestAdoptionUsesTheSameThreshold:
    """「推論に使えない文書は出典としても採用しない」の不変条件。

    別々の定数に分けると、回答に 1 文字も寄与しない文書が出典一覧にだけ
    載る状態が戻ってくる。
    """

    def test_weather_query_is_rejected_at_the_new_threshold(self):
        """実測シナリオ: 天気の質問（最良 0.6009）は 0 件で返ること。

        以前は 0.55 だったため `ec_faq.csv`（配送・支払いの FAQ）が出典に
        載っていた。0.64 なら不採用になり、出典が Web のみ＝`web_only=True`
        となって ④' の `force_judge` 経路も開く。
        """
        result = _run({"ec_faq_anthropic": 0.6009, "saas_docs_anthropic": 0.5797})

        assert not result.output, "範囲外質問で社内出典を採用してはいけない"

    def test_business_query_is_still_adopted(self):
        """業務質問（最良 0.6650）は従来どおり採用されること。"""
        result = _run({"saas_docs_anthropic": TP_FLOOR})

        assert result.output
        assert result.output[0]["score"] == pytest.approx(TP_FLOOR)

    def test_threshold_change_moves_the_adoption_side_too(self):
        """設定を動かせば採用側も追随すること（定数を分けていない証拠）。"""
        scores = {"ec_faq_anthropic": 0.6009}

        assert not _run(scores).output                       # 既定 0.64 → 不採用
        assert _run(scores, threshold=0.55).output           # 旧値 0.55 → 採用


# =============================================================================
# helpers
# =============================================================================

def _run(top_scores: dict, *, threshold: float = None):
    """コレクションごとの Top スコアを与えて `RAGSearchTool.execute()` を回す。"""
    config = GraceConfig()
    if threshold is not None:
        config.executor.reasoning_min_rag_score = threshold
    config.qdrant.restrict_to_collection = False
    config.qdrant.allowed_collections = []

    tool = RAGSearchTool.__new__(RAGSearchTool)
    tool.config = config
    tool.qdrant_url = config.qdrant.url
    tool._client = None
    tool.keyword_extractor = None

    def _search(_query, collection, **_kwargs):
        score = top_scores.get(collection)
        if score is None:
            return []
        return [
            {"score": score, "id": 1, "payload": {"answer": "本文", "source": "x.csv"}},
            {"score": score - 0.01, "id": 2, "payload": {"answer": "本文2", "source": "x.csv"}},
        ]

    with patch.object(
        RAGSearchTool, "_get_all_collections_dynamic", lambda _self, **_kw: list(top_scores)
    ), patch("agent_tools.search_rag_knowledge_base_structured", side_effect=_search), \
         patch.object(RAGSearchTool, "_embed_query_once", return_value=(None, None)):
        return tool.execute(query="テスト質問")
