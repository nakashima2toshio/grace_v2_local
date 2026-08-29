# backend/tests/test_policy_claims.py
"""方針文（担当範囲外の断り・窓口案内）を支持率の母数から外すこと。

## 何を守っているか

`SCOPE_POLICY` は担当範囲外の話題について「範囲外である旨を明示し、窓口を案内
する」ことを求めている。方針どおり断ると、その断り文は claim として抽出され、
**社内ナレッジには載っていないので必ず neutral** になる。

neutral は support_rate の分子にも分母にも入らないが、M-6 の判定率減衰
（`decided / total`）の **total には入る**。つまり**正しく断るほど信頼度が
下がる**。実測 2026-08-29（クラウド版・住民票＋天気）:

    supported 7 / neutral 2（「天気は担当範囲外」「気象庁のURL」）
    → decided 7/9 → damped 0.992 → final 0.906

住民票への回答は 7/7 すべて supported なのに、正しい断りが 2 件あるという
理由だけで 0.99 → 0.91 へ落ちていた。
"""
from __future__ import annotations

from types import SimpleNamespace

from grace.confidence import POLICY_CLAIM_MARKERS, is_unsupportable_policy_claim


def _claim(text: str, verdict: str = "neutral") -> SimpleNamespace:
    return SimpleNamespace(claim=text, verdict=verdict)


class TestIsUnsupportablePolicyClaim:
    def test_担当範囲外の断りは除外対象(self):
        assert is_unsupportable_policy_claim(_claim("天気・気象情報は当窓口の担当範囲外である"))

    def test_窓口案内は除外対象(self):
        assert is_unsupportable_policy_claim(
            _claim("詳細は各分野の担当窓口へお問い合わせください")
        )

    def test_supportedなら除外しない(self):
        """情報源に裏付けのある記述は、語が一致しても母数から落とさない。

        「住民票は市役所の窓口で取得できます」のような**事実**まで落とすと、
        支持率の分子まで減って評価が歪む。
        """
        assert not is_unsupportable_policy_claim(
            _claim("不明点は窓口へお問い合わせください", verdict="supported")
        )

    def test_contradictedなら除外しない(self):
        """矛盾は必ず残す。cap（answer_conf=0.30）の根拠を消してはならない。"""
        assert not is_unsupportable_policy_claim(
            _claim("担当範囲外である", verdict="contradicted")
        )

    def test_通常の事実主張は除外しない(self):
        for text in (
            "住民票の写しは市役所本庁舎・各区役所の窓口で取得できる",
            "窓口・コンビニでの取得手数料は1通300円である",
            "住民票の写しの取得には本人確認書類が必要である",
        ):
            assert not is_unsupportable_policy_claim(_claim(text)), text

    def test_マーカーは空でない(self):
        assert POLICY_CLAIM_MARKERS
        assert all(m for m in POLICY_CLAIM_MARKERS)


class TestAggregation:
    """実測ケース（住民票 7 supported ＋ 断り 2 neutral）の集計。"""

    CLAIMS = [
        _claim("住民票の写しは市役所本庁舎・各区役所の窓口で取得できる", "supported"),
        _claim("住民票の写しは郵送で取得できる", "supported"),
        _claim("住民票の写しはコンビニ交付で取得できる", "supported"),
        _claim("コンビニ交付にはマイナンバーカードが必要", "supported"),
        _claim("窓口・コンビニでの取得費用は1通300円", "supported"),
        _claim("郵送での支払いは定額小為替", "supported"),
        _claim("窓口・郵送の場合は本人確認書類が必要", "supported"),
        _claim("天気・気象情報は当窓口の担当範囲外である"),
        _claim("天気予報は気象庁公式サイトをご利用ください"),
    ]

    def test_方針文を除いた母数になる(self):
        scored = [c for c in self.CLAIMS if not is_unsupportable_policy_claim(c)]
        assert len(scored) == 7, "断り 2 件が母数から外れる"
        assert all(c.verdict == "supported" for c in scored)
        # decided/total = 7/7 = 1.0 → M-6 減衰なし（従来は 7/9 で減衰していた）
        decided = sum(1 for c in scored if c.verdict in ("supported", "contradicted"))
        assert decided == len(scored)

    def test_全部が方針文なら除外しない(self):
        """除外すると検証対象が 0 になり「未検証」へ倒れてしまう。

        範囲外の質問に断りだけを返した場合が該当する。後段の ④'
        （情報なし回答検知）が実質回答かどうかを見るので、そちらに委ねる。
        """
        only_policy = [c for c in self.CLAIMS if is_unsupportable_policy_claim(c)]
        assert len(only_policy) == 2
        scored = [c for c in only_policy if not is_unsupportable_policy_claim(c)]
        assert scored == [], "この状態を verify() が検知して全件集計へ倒す"
