"""P4 実行メモリ層のテスト（API 非依存・決定的）。"""

from unittest.mock import MagicMock, patch

from grace.config import GraceConfig, MemoryConfig, ToolsConfig
from grace.executor import Executor
from grace.memory import ExecutionMemory, extract_keywords
from grace.planner import Planner
from grace.schemas import ExecutionPlan, PlanStep
from grace.tools import ToolRegistry, ToolResult


class TestKeywords:
    def test_extract_keywords(self):
        kw = extract_keywords("Pythonの非同期処理について")
        assert "python" in kw
        assert all(len(k) >= 2 for k in kw)

    def test_empty(self):
        assert extract_keywords("") == []


class TestExecutionMemory:
    def test_record_and_load(self, tmp_path):
        mem = ExecutionMemory(str(tmp_path / "mem.jsonl"))
        mem.record("質問A", "colA", success=True, confidence=0.9)
        mem.record("質問B", "colB", success=False, confidence=0.2)
        records = mem.load()
        assert len(records) == 2
        assert records[0].collection == "colA"
        assert records[0].success is True

    def test_load_missing_file(self, tmp_path):
        assert ExecutionMemory(str(tmp_path / "nope.jsonl")).load() == []

    def test_collection_priors_ranking(self, tmp_path):
        mem = ExecutionMemory(str(tmp_path / "mem.jsonl"))
        # colA: 高成功・高信頼、colB: 低調
        for _ in range(5):
            mem.record("ニュースの質問", "colA", success=True, confidence=0.9)
        for _ in range(5):
            mem.record("ニュースの質問", "colB", success=False, confidence=0.3)
        priors = mem.collection_priors(query="ニュースの質問")
        assert priors[0].collection == "colA"
        assert priors[0].score() > priors[1].score()

    def test_best_collection_threshold(self, tmp_path):
        mem = ExecutionMemory(str(tmp_path / "mem.jsonl"))
        # 実績不足（count<min_count）→ None
        mem.record("q", "colA", success=True, confidence=0.9)
        assert mem.best_collection(min_count=3, min_score=0.5) is None
        # 実績を増やす → 採用
        for _ in range(3):
            mem.record("q", "colA", success=True, confidence=0.9)
        assert mem.best_collection(min_count=3, min_score=0.5) == "colA"

    def test_record_many_dedup(self, tmp_path):
        mem = ExecutionMemory(str(tmp_path / "mem.jsonl"))
        mem.record_many("q", ["colA", "colA", "colB"], success=True, confidence=0.8)
        cols = sorted({r.collection for r in mem.load()})
        assert cols == ["colA", "colB"]
        assert len(mem.load()) == 2  # colA 重複は1回


class TestPlannerMemoryBias:
    def test_rule_based_plan_uses_prior(self, tmp_path):
        path = str(tmp_path / "mem.jsonl")
        mem = ExecutionMemory(path)
        for _ in range(4):
            mem.record("テスト質問", "cc_news_2per_anthropic", success=True, confidence=0.9)

        cfg = GraceConfig(memory=MemoryConfig(path=path, min_count=3, min_score=0.5))
        with patch("grace.planner.create_chat_client", return_value=MagicMock()):
            planner = Planner(config=cfg)
            plan = planner.create_plan("テスト質問")
        rag = next(s for s in plan.steps if s.action == "rag_search")
        assert rag.collection == "cc_news_2per_anthropic"

    def test_rule_based_plan_no_prior_is_none(self, tmp_path):
        cfg = GraceConfig(memory=MemoryConfig(path=str(tmp_path / "empty.jsonl")))
        with patch("grace.planner.create_chat_client", return_value=MagicMock()):
            planner = Planner(config=cfg)
            plan = planner.create_plan("テスト質問")
        rag = next(s for s in plan.steps if s.action == "rag_search")
        assert rag.collection is None


class TestExecutorMemoryRecording:
    def _registry(self):
        registry = MagicMock(spec=ToolRegistry)
        rag = MagicMock()
        rag.execute.return_value = ToolResult(
            success=True,
            output=[{"id": 1, "score": 0.9, "payload": {"question": "Q", "answer": "A"}}],
            confidence_factors={"result_count": 1, "avg_score": 0.9,
                                "used_collection": "colX"},
        )
        reasoning = MagicMock()
        reasoning.execute.return_value = ToolResult(
            success=True, output="回答", confidence_factors={"has_sources": True})

        def get(name):
            return {"rag_search": rag, "reasoning": reasoning}.get(name)
        registry.get.side_effect = get
        return registry

    def test_execution_records_used_collection(self, tmp_path):
        path = str(tmp_path / "mem.jsonl")
        cfg = GraceConfig(
            memory=MemoryConfig(path=path),
            tools=ToolsConfig(enabled=["rag_search", "reasoning"]),
        )
        plan = ExecutionPlan(
            original_query="質問X", complexity=0.3, estimated_steps=2,
            requires_confirmation=False,
            steps=[
                PlanStep(step_id=1, action="rag_search", description="検索",
                         query="質問X", expected_output="r"),
                PlanStep(step_id=2, action="reasoning", description="回答",
                         depends_on=[1], expected_output="a"),
            ],
            success_criteria="ok", plan_id="mem-test",
        )
        with patch.object(Executor, "_evaluate_rag_relevance", return_value=True):
            executor = Executor(tool_registry=self._registry(), config=cfg)
            executor.execute_plan(plan)

        records = ExecutionMemory(path).load()
        assert any(r.collection == "colX" for r in records)
