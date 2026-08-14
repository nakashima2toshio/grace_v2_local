# backend/tests/test_replan_query_hygiene.py
"""リプラン時のクエリ汚染と、実行不能な計画の採用を防ぐテスト。

## 何を守っているのか

リプランの補足情報（前回のエラー・進捗・指示文）が **元のクエリへ連結**
されていたため、次の 2 段構えで悪化していた。

1. 連結後の文字列がそのまま `PlanStep.query` になり、rag_search の
   検索クエリが「明日の東京の天気は？\\n\\n【追加情報】\\n注意: 前回の試行で…」
   になる。embedding が壊れて再検索も外す。
2. `estimate_complexity` は **長さで加点する**（>100 で +0.1、>200 で +0.1）
   ため、連結したぶん複雑度が閾値 0.7 を越え、ルールベース計画
   （LLM 呼び出し 0 回）から高コストな LLM 計画生成へ落ちる。
   ローカル LLM ではここだけで数百秒かかる。

  → 汚染 → 複雑度上昇 → 高コスト経路 → 失敗 → リプラン、の自己増幅ループ。

加えて、依存先が存在しない計画が警告だけで採用されていた。Executor は
依存先の結果が無い限りステップを実行しないため、そのステップは**永久に
実行されない**（reasoning なら回答が出ないまま計画が完走する）。
"""
from __future__ import annotations

from grace.config import GraceConfig
from grace.planner import Planner
from grace.replan import ReplanContext, ReplanManager, ReplanTrigger
from grace.schemas import ExecutionPlan, PlanStep, repair_plan_dependencies


def _planner() -> Planner:
    """LLM もメモリも使わない Planner（ルールベース経路だけを見る）。"""
    planner = Planner.__new__(Planner)
    planner.config = GraceConfig()
    planner.model_name = "qwen3.5:9b"
    planner.client = None
    planner._memory = None
    return planner


def _step(step_id: int, depends_on: list[int], action: str = "reasoning") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=action,
        description=f"step {step_id}",
        expected_output="o",
        depends_on=depends_on,
    )


def _plan(*steps: PlanStep) -> ExecutionPlan:
    return ExecutionPlan(
        original_query="q",
        complexity=0.5,
        estimated_steps=len(steps),
        requires_confirmation=False,
        steps=list(steps),
        success_criteria="c",
    )


# =============================================================================
# ① 検索クエリが汚染されない
# =============================================================================

class TestSearchQueryStaysClean:

    def test_rule_based_plan_uses_the_raw_query(self):
        plan = _planner().create_plan("明日の東京の天気は？")
        rag = [s for s in plan.steps if s.action == "rag_search"]
        assert rag, "rag_search ステップが無い"
        assert rag[0].query == "明日の東京の天気は？"

    def test_context_hints_never_reach_the_step_query(self):
        """ヒントを渡しても `PlanStep.query` に混ざらないこと。

        LLM 計画生成は使えないのでフォールバック計画を見る。
        """
        planner = _planner()
        plan = planner._create_fallback_plan("明日の東京の天気は？")
        for step in plan.steps:
            assert "【追加情報】" not in (step.query or "")
            assert "前回の試行" not in (step.query or "")

    def test_hints_are_not_concatenated_into_the_query(self):
        """`_build_context_hints` が元のクエリを含まないこと。

        含んでいたら、どこかで連結されている（旧 `_enhance_query_with_context`
        は `f"{original_query}\\n\\n【追加情報】…"` を返していた）。
        """
        manager = ReplanManager(config=GraceConfig())
        hints = manager._build_context_hints(
            ReplanContext(
                trigger=ReplanTrigger.TIMEOUT,
                original_query="明日の東京の天気は？",
                error_message="タイムアウト",
            )
        )
        assert "明日の東京の天気は？" not in hints
        assert "タイムアウト" in hints

    def test_partial_replan_hints_are_not_an_instruction_query(self):
        """部分再計画の指示文がクエリにならないこと。

        以前は「以下の計画の続きを作成してください。元の質問: …」という
        指示文まるごとが検索クエリになっていた。
        """
        manager = ReplanManager(config=GraceConfig())
        hints = manager._create_remaining_hints(
            ReplanContext(
                trigger=ReplanTrigger.TIMEOUT,
                original_query="明日の東京の天気は？",
                error_message="タイムアウト",
            ),
            completed_steps=[_step(1, [])],
        )
        assert "明日の東京の天気は？" not in hints
        assert "再計画" in hints


class TestComplexityIsNotInflatedByHints:
    """長さ加点による経路切り替えが起きないこと。"""

    def test_short_query_stays_on_the_rule_based_path(self):
        planner = _planner()
        query = "明日の東京の天気は？"
        assert planner.estimate_complexity(query) < 0.7

    def test_concatenated_hints_would_have_inflated_complexity(self):
        """**汚染していたら**複雑度が上がることを示す（回帰の証拠）。

        このテストは修正後も通る。示したいのは「連結が危険だった」ことで、
        だからこそ `context_hints` を別経路にしてある。
        """
        planner = _planner()
        query = "明日の東京の天気は？"
        polluted = (
            f"{query}\n\n【追加情報】\n"
            "注意: 前回の試行で「ステップ 2 (reasoning) が 30 秒でタイムアウト"
            "しました」というエラーが発生\n進捗: ステップ1は完了済み"
        )
        assert planner.estimate_complexity(polluted) > planner.estimate_complexity(query)


# =============================================================================
# ② 実行不能な依存を採用しない
# =============================================================================

class TestPlanDependencyRepair:

    def test_removes_dangling_dependency(self):
        """存在しないステップ ID への依存を落とすこと。

        残すと `_check_dependencies` が永久に False を返し、そのステップは
        実行されない（実測の `Step 4: 存在しない依存先 3`）。
        """
        plan = _plan(_step(1, []), _step(2, []), _step(4, [3]))
        repairs = repair_plan_dependencies(plan)

        assert plan.steps[2].depends_on == []
        assert any("3" in r for r in repairs)

    def test_removes_backward_dependency(self):
        plan = _plan(_step(1, []), _step(2, [3]), _step(3, []))
        repair_plan_dependencies(plan)
        assert plan.steps[1].depends_on == []

    def test_removes_self_dependency(self):
        plan = _plan(_step(1, []), _step(2, [2]))
        repair_plan_dependencies(plan)
        assert plan.steps[1].depends_on == []

    def test_keeps_valid_dependencies(self):
        plan = _plan(_step(1, []), _step(2, [1]), _step(3, [1, 2]))
        repairs = repair_plan_dependencies(plan)

        assert repairs == []
        assert plan.steps[1].depends_on == [1]
        assert plan.steps[2].depends_on == [1, 2]

    def test_keeps_the_step_itself(self):
        """依存だけ落とし、**ステップは残す**こと。

        落としてしまうと reasoning ごと消えて回答が出なくなる。
        """
        plan = _plan(_step(1, []), _step(2, [99]))
        repair_plan_dependencies(plan)
        assert len(plan.steps) == 2

    def test_finalize_plan_repairs_before_returning(self):
        """`_finalize_plan` を通ると壊れた依存が残らないこと。"""
        planner = _planner()
        plan = planner._finalize_plan(_plan(_step(1, []), _step(4, [3])), 0.5)

        step_ids = {s.step_id for s in plan.steps}
        for step in plan.steps:
            for dep in step.depends_on:
                assert dep in step_ids
                assert dep < step.step_id
