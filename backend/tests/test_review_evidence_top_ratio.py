# backend/tests/test_review_evidence_top_ratio.py
"""**他ルールの条文を根拠に混ぜない**ことを固定するテスト。

## 背景（実測 2026-08-18 22:38 / 条文コレクション登録の直後）

`ec_ad_rules_anthropic` を登録して、狙いどおり全 7 ルールが `規程 5 件` を引くように
なった（それまでは 7 中 6 が `規程 0 件`）。ところが**引いた 5 件のうち 4 件が
別ルールの条文**だった。

    doc/tokusho-01: 文書全体で判定 / 規程 5 件
      [規程] 特定商取引法 第11条（販売価格・送料の明示）      0.8590  ← 本来の 1 件
      [規程] 特定商取引法 第11条（代金の支払時期・方法）      0.7422  ← 別ルール
      [規程] 特定商取引法 第11条（事業者名・住所・連絡先）    0.7374  ← 別ルール
      [規程] 特定商取引法 第11条（商品の引渡時期）            0.7371  ← 別ルール
      [規程] 社内規程（表示内容と社内規程の不一致）          0.7352  ← 別ルール

絶対閾値 0.70（`DEFAULT_EVIDENCE_MIN_SCORE`）は 1 件も落とせない。コレクションの
中身が「互いに似た条文 22 行」なので、**どのルールで検索しても他ルールが 0.70 を
超える**のは構造的にそうなる。

## これは表示が汚れるだけの問題ではない

同じ実行で tokusho-02（代金の支払時期・方法）の指摘文がこうなっていた。

    代金の支払時期および支払方法（…）の記載がなく、**また商品の引渡時期
    （発送時期・お届け目安）の記載もありません。**

引渡時期は tokusho-03 の主題で、tokusho-03 も別途発火している。**同じ事実が
2 回数えられた**。原因は ③ Detect へ渡す【規程】に tokusho-03 の条文が
混ざっていたこと。#88 / #90 でプロンプトに「主題だけ見よ」と書いたが、根拠に
他ルールの主題が入っている以上、プロンプトだけでは押さえ切れない。

## 相対比で切る

全 7 ルールの実測では、本来の条文と他ルールの条文がきれいに分離していた。

    本来の条文（各ルールの Top）… 0.8496 〜 0.9380
    他ルールの条文              … 0.7057 〜 0.7569
    → 谷は 0.7569 〜 0.8496

`DEFAULT_EVIDENCE_TOP_RATIO = 0.92` は、最小の Top（0.8496）に掛けて 0.7816。
谷のほぼ中央で、下端まで 0.0247／上端まで 0.0680 の余裕がある。

**絶対値（例: 0.80）ではなく比にする理由**は、条文を実データへ差し替えたり行を
分割したりするとスコアの絶対水準がまとめて動くから。動いても「本来 vs 無関係」の
比は残るので、差し替えのたびの再調整が要らない。

ここで固定すること:
  1. Top から離れた規程を根拠にしないこと（実測値そのままで）
  2. 絶対閾値との併用で、どちらか一方でも外れたら落とすこと
  3. Top 1 件だけのときに自分自身を落とさないこと
  4. 僅差で並ぶ規程は**両方残す**こと（見落としを作らない）
  5. 落としたことが実行ログに出ること（絶対閾値と区別できる文言で）
  6. score を持たない結果は従来どおり通すこと（後方互換）
  7. RuleSet 側で比を上書きできること

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from backend.app.core.review_agent import _retrieve_evidence
from backend.app.core.rulesets import (
    DEFAULT_EVIDENCE_TOP_RATIO,
    RuleSet,
    get_ruleset,
)


def _hit(score: float, question: str, answer: str = "本文") -> dict:
    return {"score": score, "payload": {"question": question, "answer": answer}}


# 実測 2026-08-18 22:38 の tokusho-01 の根拠 5 件（スコアも文言も実測値）。
TOKUSHO_01_HITS = [
    _hit(0.8590, "特定商取引法 第11条（販売価格・送料の明示）",
         "通信販売の広告には、商品の販売価格（消費税込み）を表示し、"
         "送料が別途必要な場合はその額を明示しなければならない。"),
    _hit(0.7422, "特定商取引法 第11条（代金の支払時期・方法）",
         "代金の支払時期および支払方法を表示しなければならない。"),
    _hit(0.7374, "特定商取引法 第11条（事業者名・住所・連絡先）",
         "事業者の氏名・住所・電話番号を表示しなければならない。"),
    _hit(0.7371, "特定商取引法 第11条（商品の引渡時期）",
         "商品の引渡時期を表示しなければならない。"),
    _hit(0.7352, "社内規程（表示内容と社内規程の不一致）",
         "広告に表示した取引条件が社内規程と食い違っていないかを確認する。"),
]

OWN_RULE_CITATION = "[規程] 特定商取引法 第11条（販売価格・送料の明示）"


def _tool(output):
    """rag_search が `output` を返すツールレジストリのスタブ。"""
    class _Registry:
        def execute(self, _name, **_kwargs):
            class _Res:
                success = True
            res = _Res()
            res.output = output
            return res
    return _Registry()


def _ruleset(**overrides) -> RuleSet:
    rs = get_ruleset("ec_ad")
    fields = {
        "id": rs.id, "name": rs.name, "collections": list(rs.collections),
        "rules": list(rs.rules), "critical_keywords": list(rs.critical_keywords),
    }
    fields.update(overrides)
    return RuleSet(**fields)


# =============================================================================
# ① 実測の再現
# =============================================================================

class TestOtherRulesAreNotCited:

    def test_only_the_own_rule_survives(self):
        """実測 5 件のうち、本来の条文 1 件だけが残る。"""
        citations, sources = _retrieve_evidence(
            _tool(TOKUSHO_01_HITS),
            "販売価格・送料の明示 通信販売の広告には…",
            _ruleset(),
        )

        assert citations == [OWN_RULE_CITATION], (
            f"他ルールの条文が根拠に混ざっている: {citations}"
        )
        assert len(sources) == 1

    def test_other_rules_subjects_do_not_reach_the_detector(self):
        """④ Ground / ③ Detect へ渡る本文に他ルールの主題が入らないこと。

        ⚠️ **ここが tokusho-02 の指摘文が引渡時期まで書いた原因。**
        表示（citations）だけ絞っても、`source_texts` に残っていれば
        【規程】経由で越境する。
        """
        _citations, sources = _retrieve_evidence(
            _tool(TOKUSHO_01_HITS),
            "販売価格・送料の明示 通信販売の広告には…",
            _ruleset(),
        )

        joined = "\n".join(sources)
        assert "引渡時期" not in joined
        assert "支払時期" not in joined

    def test_absolute_threshold_still_applies(self):
        """比を満たしても絶対閾値未満なら落とす（併用であること）。

        Top が低いときに「Top に近い」だけで低スコアが通ってはいけない。
        """
        citations, _sources = _retrieve_evidence(
            _tool([_hit(0.6847, "返品規定を教えてください"),
                   _hit(0.6800, "返金ポリシーを教えてください")]),
            "販売価格・送料の明示", _ruleset(),
        )

        assert citations == [], "0.70 未満が比の判定で生き残っている"


# =============================================================================
# ② 見落としを作らない
# =============================================================================

class TestRelevantEvidenceIsKept:

    def test_single_hit_never_drops_itself(self):
        """Top 1 件だけのとき、自分自身が Top なので必ず残る。"""
        citations, sources = _retrieve_evidence(
            _tool([_hit(0.7100, "特定商取引法 第11条（返品特約）")]),
            "返品特約の表示", _ruleset(),
        )

        assert citations == ["[規程] 特定商取引法 第11条（返品特約）"]
        assert len(sources) == 1

    def test_close_scores_are_all_kept(self):
        """僅差で並ぶ規程は両方残す。

        条文を実データへ差し替えて 1 条を複数行に分けた場合、同じルールの
        条文が僅差で並ぶ。これを落とすと根拠が欠ける。
        """
        citations, _sources = _retrieve_evidence(
            _tool([_hit(0.8600, "特定商取引法 第11条（販売価格・送料の明示）前段"),
                   _hit(0.8500, "特定商取引法 第11条（販売価格・送料の明示）後段"),
                   _hit(0.8100, "特定商取引法 第11条（販売価格・送料の明示）解説")]),
            "販売価格・送料の明示", _ruleset(),
        )

        # 0.8600 * 0.92 = 0.7912 → 3 件とも上回る
        assert len(citations) == 3

    def test_entries_without_score_are_kept(self):
        """score を持たない結果は従来どおり通す（後方互換）。"""
        citations, sources = _retrieve_evidence(
            _tool([{"payload": {"title": "特定商取引法 第11条", "answer": "本文"}}]),
            "販売価格", _ruleset(),
        )

        assert citations == ["[規程] 特定商取引法 第11条"]
        assert sources == ["本文"]


# =============================================================================
# ③ 設定と観測
# =============================================================================

class TestConfigurationAndLogging:

    def test_ratio_is_overridable_per_ruleset(self):
        """比を 0 にすれば従来（絶対閾値のみ）の挙動へ戻せる。"""
        citations, _sources = _retrieve_evidence(
            _tool(TOKUSHO_01_HITS), "販売価格・送料の明示",
            _ruleset(evidence_top_ratio=0.0),
        )

        assert len(citations) == 5, "比を無効化しても 5 件に戻らない"

    def test_default_ratio_sits_in_the_measured_gap(self):
        """既定値が実測の谷（0.7569 〜 0.8496）に入ること。"""
        cutoff = 0.8496 * DEFAULT_EVIDENCE_TOP_RATIO

        assert 0.7569 < cutoff < 0.8496, f"谷から外れている: {cutoff:.4f}"
        assert _ruleset().evidence_top_ratio == DEFAULT_EVIDENCE_TOP_RATIO

    def test_dropped_by_ratio_is_reported_distinctly(self):
        """絶対閾値で落としたのか、比で落としたのかを区別できること。

        ⚠️ 同じ文言だと「0.70 未満だった」と誤読され、閾値を下げる方向の
        調整に誘導してしまう（実際に落ちたのは 0.73〜0.74）。
        """
        messages: list[str] = []

        _retrieve_evidence(
            _tool(TOKUSHO_01_HITS), "販売価格・送料の明示", _ruleset(),
            on_drop=messages.append,
        )

        [message] = messages
        assert "最上位より離れた規程" in message
        assert "関連度が低い" not in message, "絶対閾値の文言と混同している"
        assert "0.7422" in message, "落とした規程のスコアが分からない"
        # 0.8590（この fixture の Top）* 0.92 = 0.7903
        assert "0.7903" in message, "どのカットオフで落としたのかが分からない"

    def test_both_reasons_are_reported_separately(self):
        """絶対閾値と比の両方で落ちたときは 2 本のログが出る。"""
        messages: list[str] = []

        _retrieve_evidence(
            _tool(TOKUSHO_01_HITS + [_hit(0.6500, "返品規定を教えてください")]),
            "販売価格・送料の明示", _ruleset(), on_drop=messages.append,
        )

        assert len(messages) == 2, f"ログが分かれていない: {messages}"
        assert any("関連度が低い" in m for m in messages)
        assert any("最上位より離れた規程" in m for m in messages)

    def test_no_message_when_nothing_is_dropped(self):
        messages: list[str] = []

        _retrieve_evidence(
            _tool([_hit(0.8600, "特定商取引法 第11条（販売価格・送料の明示）")]),
            "販売価格・送料の明示", _ruleset(), on_drop=messages.append,
        )

        assert messages == []
