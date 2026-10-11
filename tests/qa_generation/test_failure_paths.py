# tests/qa_generation/test_failure_paths.py
"""Q/A 生成で、処理対象と失敗の扱いが静かに食い違わないこと（2026-10-11 に見つけた 2 件）。

## なぜこのテストがあるか

1. **`QAPipeline(dataset_name=..., max_docs=N)` で `max_docs` が効かなかった。**
   `load_data()` が `input_file` のときだけ `head(max_docs)` をかけていたため、
   `--dataset X --max-docs N` で試しても全件を処理していた（`qa_generation/docs/pipeline.md` §4.1.6）。
2. **Celery では、LLM が失敗したチャンクが「0 件で処理済み」になっていた。**
   `SmartQAGenerator.process_chunk()` は例外を握って `success=False` を返すので、
   ワーカーのタスクは正常終了し、`autoretry_for` の再試行が一度も効かなかった。
   さらに `collect_results()` が成功として逐次保存ファイルへ記録するため、
   再開しても作り直されなかった（同期の経路は失敗を記録しないので作り直す）。
   失敗したらタスクを失敗させ、再試行のうえで記録しないようにした。

**実 LLM・Redis・Celery ワーカーは不要**（生成器はスタブ、タスクは `apply()` でその場で実行する）。
"""
from __future__ import annotations

import pandas as pd

import celery_tasks
from qa_generation.pipeline import QAPipeline

# =============================================================================
# 1. dataset_name でも max_docs が効く
# =============================================================================


def _dataset_pipeline(tmp_path, max_docs):
    """`__init__` を通さずに組み立てる（SmartQAGenerator の LLM クライアント生成を避ける）。"""
    p = QAPipeline.__new__(QAPipeline)
    p.dataset_name = "cc_news"
    p.input_file = None
    p.output_dir = str(tmp_path)
    p.text_column = None
    p.max_docs = max_docs
    p.config = p._load_config()
    return p


def _fake_dataset(monkeypatch, rows):
    import qa_generation.data_io as data_io

    df = pd.DataFrame({"Combined_Text": [f"本文{i}" for i in range(rows)]})
    monkeypatch.setattr(data_io, "load_preprocessed_data", lambda name: df.copy())


def test_データセットでもmax_docsで先頭に絞る(monkeypatch, tmp_path):
    _fake_dataset(monkeypatch, rows=5)
    df = _dataset_pipeline(tmp_path, max_docs=2).load_data()
    assert list(df["Combined_Text"]) == ["本文0", "本文1"]


def test_データセットでmax_docsが無ければ全件(monkeypatch, tmp_path):
    _fake_dataset(monkeypatch, rows=5)
    assert len(_dataset_pipeline(tmp_path, max_docs=None).load_data()) == 5


def test_入力ファイルのmax_docsは従来どおり効く(tmp_path):
    csv = tmp_path / "doc_chunks.csv"
    pd.DataFrame({"chunk_id": ["a", "b", "c"], "text": ["一。", "二。", "三。"]}).to_csv(csv, index=False)
    p = QAPipeline.__new__(QAPipeline)
    p.dataset_name = None
    p.input_file = str(csv)
    p.output_dir = str(tmp_path)
    p.text_column = None
    p.max_docs = 2
    p.config = p._load_config()
    assert len(p.load_data()) == 2


# =============================================================================
# 2. Celery: LLM が失敗したチャンクは、再試行して失敗にし、処理済みとして記録しない
# =============================================================================


class _Generator:
    """`SmartQAGenerator.process_chunk()` と同じ形を返す。`success=False` なら失敗時の戻り値。"""

    def __init__(self, success: bool, qa_pairs=None):
        self.success = success
        self.qa_pairs = qa_pairs or []
        self.calls = 0

    def process_chunk(self, text):
        self.calls += 1
        if not self.success:
            return {"analysis": {}, "qa_pairs": [], "usage": {"input_tokens": 0, "output_tokens": 0},
                    "success": False}
        return {"analysis": {"qa_count": len(self.qa_pairs)}, "qa_pairs": self.qa_pairs,
                "usage": {"input_tokens": 3, "output_tokens": 1}, "success": True}


CHUNK = {"id": "c0", "text": "手数料は1通300円です。"}
CONFIG = {"type": "doc"}


def _run_task(monkeypatch, generator):
    monkeypatch.setattr(celery_tasks, "_get_generator", lambda model: generator)
    return celery_tasks.generate_qa_for_chunk_task.apply(args=[CHUNK, CONFIG, "m"])


def test_LLMが失敗したらタスクを失敗にする(monkeypatch):
    gen = _Generator(success=False)
    result = _run_task(monkeypatch, gen)
    assert result.failed()
    # 再試行も効く（1 回目 + max_retries 回）
    assert gen.calls == 1 + celery_tasks.generate_qa_for_chunk_task.max_retries


def test_LLMが0件と判断したのは成功のまま(monkeypatch):
    """`qa_count=0`（Q/A 不要と LLM が判断）は失敗ではない。再試行せず、処理済みにする。"""
    gen = _Generator(success=True, qa_pairs=[])
    result = _run_task(monkeypatch, gen)
    assert result.successful()
    assert result.get()["qa_pairs"] == []
    assert gen.calls == 1


def test_成功すればQAを返す(monkeypatch):
    gen = _Generator(success=True, qa_pairs=[{"question": "手数料は？", "answer": "300円", "topic": "料金"}])
    out = _run_task(monkeypatch, gen).get()
    assert out["qa_pairs"] == [{"question": "手数料は？", "answer": "300円", "chunk_id": "c0",
                                "topic": "料金", "dataset_type": "doc"}]


class _DoneTask:
    """`collect_results()` が見る AsyncResult の最小限（ready / get）。"""

    def __init__(self, eager):
        self._eager = eager

    def ready(self):
        return True

    def get(self, timeout=None):
        return self._eager.get(propagate=True)


def test_失敗したチャンクは逐次保存ファイルに記録しない(monkeypatch, tmp_path):
    """パイプラインの Celery 経路を通し、失敗したチャンクが再開で作り直される状態であること。"""
    ok = _Generator(success=True, qa_pairs=[{"question": "Q", "answer": "A"}])
    ng = _Generator(success=False)

    def fake_submit(chunks, config, model):
        tasks = []
        for chunk in chunks:
            monkeypatch.setattr(celery_tasks, "_get_generator", lambda m, c=chunk: ng if c["id"] == "c1" else ok)
            tasks.append(_DoneTask(celery_tasks.generate_qa_for_chunk_task.apply(args=[chunk, config, model])))
        return tasks

    monkeypatch.setattr(celery_tasks, "check_celery_workers", lambda workers: True)
    monkeypatch.setattr(celery_tasks, "submit_unified_qa_generation", fake_submit)

    p = QAPipeline.__new__(QAPipeline)
    p.dataset_name = None
    p.input_file = "doc.csv"
    p.output_dir = str(tmp_path)
    p.text_column = None
    p.max_docs = None
    p.model = "m"
    p.config = {"type": "doc"}
    chunks = [{"id": "c0", "text": "一。"}, {"id": "c1", "text": "二。"}, {"id": "c2", "text": "三。"}]

    qa_pairs = p._generate_with_celery(chunks, workers=1, concurrency=1)

    assert [q["chunk_id"] for q in qa_pairs] == ["c0", "c2"]
    assert set(p._load_progress()) == {"c0", "c2"}     # c1 は記録しない → 再開で作り直す

