# backend/tests/test_review_undecided_groundedness.py
"""**「判定できなかった」を「支持率 0」と混同しない**ことを固定するテスト。

## 背景（実測 2026-08-17 20:08:30 / GRACE-Review）

    [groundedness] 判定内訳 — neutral / neutral / neutral / neutral / neutral
    [rescue]   tokusho-01: 矛盾なし・根拠ありのため保留として維持
    結果: 確信度 0.00 / 状態: 要確認

全 5 主張が neutral（＝規程で支持も否定もされていない）。それを **確信度 0.00**
として扱っていた。

## なぜ 0.0 になるのか

`GroundednessVerifier.verify`（`grace/confidence.py`）:

    decided = supported + contradicted
    support_rate = (supported / decided) if decided > 0 else 0.0
    ...
    verified=total > 0,

- `support_rate` は分母 0 なので **0.0**。だがこれは「1 件も支持されなかった」では
  なく**測れていない**。
- `verified = total > 0` は「LLM が主張を**分解**できたか」であって「支持／矛盾の
  **判定が付いたか**」ではない。全 neutral でも `True` で通る。

その結果 `decide_finding_status(0.0, verified=True, …)` は `confirm_th`（0.60）を
下回るので必ず `suppressed` へ倒れ、救済（矛盾なし・根拠あり）に拾われて
`review_required` へ戻ってくる。**救済が効かない条件（指摘文が空・根拠ゼロ）では、
判定できていない指摘が黙って消える。**

## Support 側は既にこの区別をしている

    # grace/executor.py
    decided = getattr(gres, "supported", 0) + getattr(gres, "contradicted", 0)
    if not gres.verified or decided == 0:
        # 未検証 or 判定不能: … support_rate=0 を信頼度の罰点に使わない

`backend/app/core/gates.py` にも「全 neutral（decided=0）や JSON 崩れ
（verified=False）→ 支持率 0.0」と明記されている。
**Review だけがこの `decided == 0` の判定を落としていた。**

## 前回の分析の訂正

当初「suppress ログの『支持率 0.50』と画面の『確信度 0.00』が食い違っている」と
報告したが、これは誤りだった。当時は `always_check` がセグメントごとに走っていた
ため（#85 で修正）**tokusho-01 が複数回判定されており、0.50 と 0.00 は別々の指摘**
だった。ログ自体は正しい。実在する欠陥は「全 neutral を支持率 0 として扱う」1 点。

ここで固定すること:
  1. 全 neutral は `review_required`（`suppressed` を経由しない）
  2. 救済に頼らないこと（救済カウントが増えない）
  3. 検証不能（`verified=False`）も従来どおり `review_required`
  4. 判定が得られたケースの挙動は変わらないこと（confirmed / suppressed）
  5. 「支持率 0.00」と書かず、判定が無いことをログに出すこと
  6. 抑止理由に支持・矛盾の内訳が出ること

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.core.review_agent import run_review_agent_core

# ⚠️ **重大リスク語を含まない文書を使う。** 「業界No.1」等を含めると
# `apply_forced_high` が強制的に `review_required` へ引き上げるため、
# status の検証が「強制 high のせい」なのか「支持率のせい」なのか区別できない。
# 「無料」は keihyo-08 のキーワードだが `critical_keywords` には入っていない。
DOC = "初回は無料でご提供します。"


def _gres(*, supported=0, contradicted=0, total=0, verified=True, reason=""):
    """`GroundednessVerifier.verify` の戻りを模した値。

    `support_rate` は本物と同じ式で出す（全 neutral なら 0.0 になる）。
    """
    decided = supported + contradicted
    return SimpleNamespace(
        support_rate=(supported / decided) if decided else 0.0,
        supported=supported,
        contradicted=contradicted,
        total=total,
        has_contradiction=contradicted > 0,
        verified=verified,
        reason=reason,
        claims=[],
    )


def _run(review_stub, gres, **kwargs):
    review_stub.groundedness = gres
    events: list = []
    result = run_review_agent_core(DOC, emit=events.append, verbose=True, **kwargs)
    return result, events


def _logs(events, step):
    return [e.message for e in events if e.type == "log" and e.step == step]


# =============================================================================
# ① 全 neutral は「未検証」として扱う
# =============================================================================

class TestAllNeutralIsUndecided:

    def test_all_neutral_becomes_review_required(self, review_stub):
        """実測の再現: 5 主張すべて neutral。"""
        result, _events = _run(review_stub, _gres(total=5))

        assert result.findings, "指摘が消えている"
        for finding in result.findings:
            assert finding.status == "review_required", (
                f"全 neutral なのに {finding.status} になっている"
            )
            assert finding.suppress_reason is None

    def test_it_does_not_go_through_suppression(self, review_stub):
        """⚠️ **救済に頼らない。**

        救済（矛盾なし・根拠あり）が効かない条件では、判定できていない指摘が
        黙って消える。最初から `review_required` に倒すのが正しい。
        """
        result, _events = _run(review_stub, _gres(total=5))

        assert result.rescued == 0, (
            f"救済で拾い直している（{result.rescued} 件）。"
            "suppressed へ落としてから戻す遠回りをしている"
        )
        assert result.summary.suppressed == 0

    def test_unverifiable_is_also_review_required(self, review_stub):
        """検証不能（JSON 崩れ等）も従来どおり残す。"""
        result, _events = _run(
            review_stub, _gres(verified=False, reason="empty response"),
        )

        assert result.findings
        assert all(f.status == "review_required" for f in result.findings)
        assert result.rescued == 0


# =============================================================================
# ② 判定が得られたケースは変えない
# =============================================================================

class TestJudgedCasesAreUnchanged:

    def test_fully_supported_is_confirmed(self, review_stub):
        result, _events = _run(review_stub, _gres(supported=4, total=4))

        assert result.findings
        assert all(f.status == "confirmed" for f in result.findings)
        assert all(f.confidence == pytest.approx(1.0) for f in result.findings)

    def test_low_support_is_still_suppressed(self, review_stub):
        """支持率が本当に低いケースは従来どおり抑止（＋救済）の対象。"""
        result, _events = _run(
            review_stub, _gres(supported=1, contradicted=4, total=5),
        )

        # 支持率 0.2 < confirm_th 0.60。矛盾ありなので救済されない。
        assert result.summary.suppressed >= 1
        assert result.rescued == 0

    def test_middle_support_is_review_required(self, review_stub):
        result, _events = _run(
            review_stub, _gres(supported=3, contradicted=1, total=4),
        )

        assert result.findings
        assert all(f.status == "review_required" for f in result.findings)


# =============================================================================
# ③ ログが誠実であること
# =============================================================================

class TestLogIsHonest:

    def test_undecided_is_not_reported_as_zero_support(self, review_stub):
        """「支持率 0.00」と書かない（測れていないだけで否定されていない）。"""
        _result, events = _run(review_stub, _gres(total=5))

        ground = _logs(events, "ground")
        assert any("判定なし（5 主張すべて neutral）" in m for m in ground), (
            f"判定が無いことがログに出ていない: {ground}"
        )
        assert not any("支持率 0.00" in m for m in ground), (
            "判定できていないのに支持率 0 と書いている"
        )

    def test_unverifiable_reports_the_reason(self, review_stub):
        _result, events = _run(
            review_stub, _gres(verified=False, reason="empty response"),
        )

        ground = _logs(events, "ground")
        assert any("検証不能" in m and "empty response" in m for m in ground), (
            f"検証不能の理由がログに出ていない: {ground}"
        )

    def test_suppress_reason_shows_the_breakdown(self, review_stub):
        """抑止理由に支持・矛盾の内訳を出す（支持率だけでは切り分けられない）。"""
        result, _events = _run(
            review_stub, _gres(supported=1, contradicted=4, total=5),
        )

        assert result.summary.suppressed >= 1
        # suppressed は findings に含まれないので、件数だけを確認したうえで
        # 理由文の生成をログから見る
        _result2, events2 = _run(
            review_stub, _gres(supported=1, contradicted=4, total=5),
        )
        suppress = _logs(events2, "suppress")
        assert any("1支持・4矛盾" in m for m in suppress), (
            f"内訳が出ていない: {suppress}"
        )
