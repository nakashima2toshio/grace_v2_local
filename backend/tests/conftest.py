# backend/tests/conftest.py
"""backend テスト共通フィクスチャ。

`run_support_agent_core` の外部依存（planner/executor/verifier/tools/LLM 分類器）を
スタブへ差し替え、API キー・Qdrant・実 LLM なしでパイプラインの配線
（イベント・HITL・判定の流れ）を検証できるようにする。判定に使う純関数
（`_answer_gate` / `_decide_action` 等）は backend/app/core/gates.py 側にあり、
本ディレクトリの test_support_agent_core.py が配線ごと固定している。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import List, Optional

import pytest


def make_config_stub(notify=0.7, confirm=0.4, default_timeout=2):
    """get_config() 互換の最小スタブ（core が触る属性のみ）。"""
    return SimpleNamespace(
        confidence=SimpleNamespace(
            thresholds=SimpleNamespace(silent=0.9, notify=notify, confirm=confirm)
        ),
        qdrant=SimpleNamespace(allowed_collections=[]),
        llm=SimpleNamespace(prompt_addendum=""),
        # W-1: 業界プロファイル由来の優先ドメインの注入先
        web_search=SimpleNamespace(preferred_domains=[], preferred_domain_boost=0.15),
        intervention=SimpleNamespace(
            default_timeout=default_timeout, auto_proceed_on_timeout=False
        ),
    )


@dataclass
class GroundednessStub:
    support_rate: float = 0.9
    supported: int = 3
    contradicted: int = 0
    total: int = 3
    verified: bool = True
    has_contradiction: bool = False


@dataclass
class StepResultStub:
    step_id: int = 1
    status: str = "success"
    sources: List[str] = field(default_factory=lambda: ["faq.md"])
    # 根拠検証用の出典本文（実 executor の StepResult.source_texts に対応）。
    # 既定は空 = 本文が取れない経路を模し、出典ラベルへのフォールバックを検証する。
    source_texts: List[str] = field(default_factory=list)


@dataclass
class PipelineStub:
    """1 シナリオ分のパイプライン外部依存の応答定義。"""

    answer: str = "パスワード再設定はマイページの「パスワードを忘れた方」から行えます。"
    sources: List[str] = field(default_factory=lambda: ["faq.md"])
    # 出典本文（groundedness 検証に渡る想定のテキスト）。空なら出典ラベルへ
    # フォールバックする従来経路を再現する。
    source_texts: List[str] = field(default_factory=list)
    # 検証器へ実際に渡されたソースを記録する（P-01 の回帰検証用）
    verify_calls: List[list] = field(default_factory=list)
    groundedness: GroundednessStub = field(default_factory=GroundednessStub)
    intent: Optional[str] = None            # 意図分類器の返答（None=分類失敗）
    no_info_verdict: Optional[bool] = False  # 実質回答判定（False=answered）
    web_output: Optional[list] = None        # ⑤ の web_search 結果
    overall_confidence: float = 0.85
    config: SimpleNamespace = field(default_factory=make_config_stub)


def install_pipeline_stub(monkeypatch, stub: PipelineStub) -> None:
    """backend.app.core.support_agent の外部依存をスタブへ差し替える。"""
    target = "backend.app.core.support_agent"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(f"{target}.get_config", lambda: stub.config)

    plan = SimpleNamespace(steps=[SimpleNamespace(step_id=1)], complexity=0.2)
    planner = SimpleNamespace(create_plan=lambda _q: plan)
    monkeypatch.setattr(f"{target}.create_planner", lambda _c: planner)

    # 実行時に stub を読む（テスト側が設置後に属性を書き換えられるよう遅延評価）
    executor = SimpleNamespace(execute=lambda _plan: SimpleNamespace(
        final_answer=stub.answer,
        step_results=[StepResultStub(
            sources=list(stub.sources),
            source_texts=list(stub.source_texts),
        )],
        overall_confidence=stub.overall_confidence,
    ))
    monkeypatch.setattr(f"{target}.create_executor", lambda _c, _r: executor)

    def _verify(_q, _a, sources):
        stub.verify_calls.append(list(sources or []))
        return stub.groundedness

    verifier = SimpleNamespace(verify=_verify)
    monkeypatch.setattr(f"{target}.create_groundedness_verifier", lambda _c: verifier)

    calc = SimpleNamespace(calculate=lambda _answers: 0.9)
    monkeypatch.setattr(
        f"{target}.create_source_agreement_calculator", lambda _c: calc
    )

    def tool_execute(name, **kwargs):
        if name == "web_search":
            return SimpleNamespace(success=True, output=stub.web_output)
        if name == "reasoning":
            return SimpleNamespace(success=True, output="Web 由来の回答")
        raise AssertionError(f"想定外のツール呼び出し: {name}")

    registry = SimpleNamespace(execute=tool_execute)
    monkeypatch.setattr(f"{target}.create_tool_registry", lambda _c: registry)

    def classify(_q: str) -> Optional[str]:
        return stub.intent

    monkeypatch.setattr(f"{target}.create_intent_classifier", lambda _c: classify)

    def judge(_q: str, _a: str) -> Optional[bool]:
        return stub.no_info_verdict

    monkeypatch.setattr(f"{target}.create_no_info_judge", lambda _c: judge)


@pytest.fixture
def pipeline_stub(monkeypatch):
    """既定シナリオ（高支持率・社内出典あり）のスタブを設置して返す。

    テスト側で stub の属性を書き換えてから core を呼べば、シナリオを変えられる
    （設置時に参照を渡しているため）。
    """
    stub = PipelineStub()
    install_pipeline_stub(monkeypatch, stub)
    return stub


# =============================================================================
# GRACE-Review（backend/app/core/review_agent.py）用スタブ
# =============================================================================

@dataclass
class ReviewPipelineStub:
    """1 シナリオ分の Review 外部依存の応答定義。

    `detect_verdicts` は「(セグメント本文, ルールID) → 判定」を決める関数を差し込む
    ためのフック。既定は「重大リスク語を含むセグメントだけ違反」とする素朴な検出器で、
    テスト側は `stub.detect = ...` で丸ごと差し替えてよい。
    """

    # 検出器へ実際に渡された (segment_text, rule_id, evidence) を記録する
    detect_calls: List[tuple] = field(default_factory=list)
    # 検証器へ渡された (query, message, sources) を記録する
    verify_calls: List[tuple] = field(default_factory=list)
    # rag_search / web_search へ渡された kwargs を記録する
    tool_calls: List[tuple] = field(default_factory=list)
    # 実行されたアクション (action_type, args)
    action_calls: List[tuple] = field(default_factory=list)
    # create_intervention_handler へ渡された kwargs（on_confirm の配線検証用）
    handler_kwargs: dict = field(default_factory=dict)

    # --- 応答の定義（テスト側で書き換える）---
    detect: Optional[object] = None          # (text, rule, evidence) -> DetectVerdict|None
    groundedness: GroundednessStub = field(default_factory=GroundednessStub)
    rag_output: Optional[list] = None        # rag_search の output（None=検索失敗）
    web_output: Optional[list] = None        # web_search の output
    mention: Optional[str] = "claim"         # 言及種別の分類結果（None=分類失敗）
    vacuous: Optional[bool] = False          # 実質性なし判定（None=判定失敗）
    confirm_continues: bool = True           # HITL CONFIRM を承認するか
    config: SimpleNamespace = field(default_factory=make_config_stub)
    # intervention ハンドラを差し替えるか。False にすると**実物**が動き、
    # InterventionBridge 経由で intervention イベントが SSE へ流れる
    # （API の HITL 往復を検証するときはこちら。`confirm_continues` は無効）。
    stub_intervention: bool = True


def install_review_stub(monkeypatch, stub: ReviewPipelineStub) -> None:
    """backend.app.core.review_agent の外部依存をスタブへ差し替える。"""
    from backend.app.core.review_gates import DetectVerdict

    target = "backend.app.core.review_agent"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(f"{target}.get_config", lambda: stub.config)

    def _default_detect(text, rule, _evidence):
        """既定: ルールのキーワードが本文に出たら違反とする（LLM 相当の代役）。"""
        for keyword in rule.keywords:
            if keyword in text:
                return DetectVerdict(
                    violates=True,
                    message=f"「{keyword}」は{rule.title}に抵触するおそれがあります",
                    suggestion="根拠の併記または表現の修正を検討してください",
                    excerpt=keyword,
                )
        return DetectVerdict(violates=False)

    def detect(text, rule, evidence):
        stub.detect_calls.append((text, rule.rule_id, evidence))
        fn = stub.detect or _default_detect
        return fn(text, rule, evidence)

    monkeypatch.setattr(f"{target}.create_violation_detector", lambda _c: detect)

    def _verify(query, message, sources):
        stub.verify_calls.append((query, message, list(sources or [])))
        return stub.groundedness

    monkeypatch.setattr(
        f"{target}.create_groundedness_verifier",
        lambda _c: SimpleNamespace(verify=_verify),
    )

    def tool_execute(name, **kwargs):
        stub.tool_calls.append((name, kwargs))
        if name == "rag_search":
            return SimpleNamespace(
                success=stub.rag_output is not None, output=stub.rag_output
            )
        if name == "web_search":
            return SimpleNamespace(
                success=stub.web_output is not None, output=stub.web_output
            )
        raise AssertionError(f"想定外のツール呼び出し: {name}")

    monkeypatch.setattr(
        f"{target}.create_tool_registry",
        lambda _c: SimpleNamespace(execute=tool_execute),
    )
    monkeypatch.setattr(
        f"{target}.create_mention_classifier", lambda _c: lambda _t: stub.mention
    )
    monkeypatch.setattr(
        f"{target}.create_vacuous_judge", lambda _c: lambda _t: stub.vacuous
    )

    handler = SimpleNamespace(
        handle=lambda _decision: SimpleNamespace(
            should_continue=stub.confirm_continues, timeout_reached=False
        )
    )

    def _make_handler(_config, **kwargs):
        stub.handler_kwargs.update(kwargs)
        return handler

    if stub.stub_intervention:
        monkeypatch.setattr(f"{target}.create_intervention_handler", _make_handler)

    def _execute_action(action_type, args):
        stub.action_calls.append((action_type, args))
        return SimpleNamespace(
            success=True, message=f"[DRY-RUN] '{action_type}' を実行", backend="dry-run"
        )

    monkeypatch.setattr(
        f"{target}.create_action_backend",
        lambda dry_run: SimpleNamespace(name="dry-run", execute=_execute_action),
    )


def _default_review_stub(**overrides) -> ReviewPipelineStub:
    """既定シナリオ（高支持率・規程ヒットあり）。"""
    return ReviewPipelineStub(
        rag_output=[{"payload": {
            "title": "景品表示法 優良誤認",
            "answer": "商品の内容について著しく優良であると示す表示は禁止される。",
        }}],
        **overrides,
    )


@pytest.fixture
def review_stub(monkeypatch):
    """既定シナリオの Review スタブを設置して返す（intervention も差し替える）。"""
    stub = _default_review_stub()
    install_review_stub(monkeypatch, stub)
    return stub


@pytest.fixture
def review_hitl_stub(monkeypatch):
    """intervention だけ**実物**を使う Review スタブ。

    API の HITL 往復（intervention イベント → POST /confirm → 実行）は
    `InterventionBridge` と実 `InterventionHandler` の組み合わせで初めて動くため、
    ハンドラを差し替えると承認待ちのイベントが流れない。Support 側の
    `pipeline_stub` がハンドラに触れていないのと同じ理由。
    """
    stub = _default_review_stub(stub_intervention=False)
    install_review_stub(monkeypatch, stub)
    return stub
