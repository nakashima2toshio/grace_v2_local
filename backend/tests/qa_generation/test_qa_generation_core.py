"""`qa_generation` の中核 3 つ（`QAPipeline.run()` / `SmartQAGenerator.process_chunk()` /
`analyze_coverage()`）を直接検証するテスト。

データ管理タブの「② Q/A 作成」と CLI（`make_qa_register_qdrant.py` / `make_qa.py`）は、
どちらも `QAPipeline.run()` を呼ぶ。にもかかわらず、`test_data_jobs.py` は
`run_qa_generation_sync` をスタブへ差し替えるため、パイプライン本体は一度も
実行されていなかった（姉妹リポジトリ grace_v2 と同じテスト。2026-10-09 に移植）。

実 LLM（Ollama）/ Embedding / ネットワークは使わない。
- LLM: `SmartQAGenerator` を偽物へ差し替える（または `client` を偽物にする）
- Embedding: `evaluation.SemanticCoverage` を、本文から固定ベクトルを返す偽物へ差し替える
- tiktoken: `cl100k_base` はダウンロードが要るので、文字数で数える偽物へ差し替える
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

import qa_generation.evaluation as evaluation
import qa_generation.pipeline as pipeline_mod
from qa_generation.pipeline import QAPipeline
from qa_generation.smart_qa_generator import (
    SmartQAGenerator,
    SmartQAPair,
    SmartQAResult,
)

# ===================================================================
# 偽物
# ===================================================================


class FakeGenerator:
    """`SmartQAGenerator` の代わり。チャンク本文ごとに決めた結果を返し、呼ばれた本文を記録する。"""

    def __init__(self, model: str = "fake-model", fail_on: tuple = ()):
        self.model = model
        self.fail_on = set(fail_on)
        self.calls: List[str] = []

    def process_chunk(self, chunk_text: str) -> Dict:
        self.calls.append(chunk_text)
        if chunk_text in self.fail_on:
            return {"analysis": {}, "qa_pairs": [],
                    "usage": {"input_tokens": 0, "output_tokens": 0}, "success": False}
        return {
            "analysis": {"qa_count": 1},
            "qa_pairs": [{"question": f"Q:{chunk_text}", "answer": f"A:{chunk_text}", "topic": "t"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "success": True,
        }


def _make_pipeline(monkeypatch, tmp_path: Path, texts: List[str], **gen_kwargs):
    csv = tmp_path / "sample_chunks.csv"
    pd.DataFrame({"chunk_id": [f"c{i}" for i in range(len(texts))], "text": texts}).to_csv(csv, index=False)
    fake = FakeGenerator(**gen_kwargs)
    monkeypatch.setattr(pipeline_mod, "SmartQAGenerator", lambda model: fake)
    out = tmp_path / "out"
    return QAPipeline(input_file=str(csv), output_dir=str(out)), fake, out


# ===================================================================
# QAPipeline.run()
# ===================================================================


def test_run_generates_saves_and_clears_progress(monkeypatch, tmp_path):
    """読み込み → 生成 → 保存が通り、4 ファイルが出て、逐次保存ファイルは消える。"""
    pipe, fake, out = _make_pipeline(monkeypatch, tmp_path, ["alpha", "beta"])

    result = pipe.run(analyze_coverage=False)

    assert result["success"] is True
    assert result["qa_count"] == 2
    assert fake.calls == ["alpha", "beta"]
    assert set(result["saved_files"]) == {"qa_json", "qa_csv", "coverage", "summary"}
    for path in result["saved_files"].values():
        assert Path(path).exists()
    qa = json.loads(Path(result["saved_files"]["qa_json"]).read_text(encoding="utf-8"))
    assert [q["chunk_id"] for q in qa] == ["c0", "c1"]
    assert {q["dataset_type"] for q in qa} == {"sample_chunks"}
    # カバレージ分析なしなら 0 のダミー
    assert result["coverage_results"]["coverage_rate"] == 0
    assert result["coverage_results"]["total_chunks"] == 2
    # 最終保存が済んだら再開用ファイルは消える
    assert not (out / "qa_progress_sample_chunks.jsonl").exists()


def test_run_resumes_from_progress_file(monkeypatch, tmp_path):
    """途中経過ファイルにあるチャンクは LLM を呼ばずに復元し、残りだけ生成する。"""
    pipe, fake, out = _make_pipeline(monkeypatch, tmp_path, ["alpha", "beta", "gamma"])
    out.mkdir(parents=True)
    restored = {"question": "Q:old", "answer": "A:old", "chunk_id": "c0"}
    (out / "qa_progress_sample_chunks.jsonl").write_text(
        json.dumps({"chunk_id": "c0", "qa_pairs": [restored]}, ensure_ascii=False) + "\n"
        + "{壊れた行\n",  # 途中クラッシュで壊れた行は読み飛ばす
        encoding="utf-8",
    )

    result = pipe.run(analyze_coverage=False)

    assert fake.calls == ["beta", "gamma"]
    assert result["qa_count"] == 3
    qa = json.loads(Path(result["saved_files"]["qa_json"]).read_text(encoding="utf-8"))
    assert qa[0] == restored


def test_failed_chunk_is_not_recorded_so_it_is_retried(monkeypatch, tmp_path):
    """生成に失敗したチャンクは途中経過に記録しない（再実行で作り直される）。"""
    pipe, fake, out = _make_pipeline(monkeypatch, tmp_path, ["alpha", "boom"], fail_on=("boom",))

    qa_pairs = pipe.generate_qa(pipe._load_chunks_from_csv(pipe.load_data()))

    assert [q["question"] for q in qa_pairs] == ["Q:alpha"]
    lines = (out / "qa_progress_sample_chunks.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["chunk_id"] for line in lines] == ["c0"]


def test_run_passes_coverage_threshold_through(monkeypatch, tmp_path):
    """`analyze_coverage=True` なら `analyze_coverage()` を呼び、閾値と種別を渡す。"""
    pipe, _fake, _out = _make_pipeline(monkeypatch, tmp_path, ["alpha"])
    seen = {}

    def fake_analyze(chunks, qa_pairs, dataset_type, custom_threshold=None):
        seen.update(n_chunks=len(chunks), n_qa=len(qa_pairs),
                    dataset_type=dataset_type, threshold=custom_threshold)
        return {"coverage_rate": 1.0, "covered_chunks": 1, "total_chunks": 1, "uncovered_chunks": []}

    monkeypatch.setattr(pipeline_mod, "analyze_coverage", fake_analyze)

    result = pipe.run(analyze_coverage=True, coverage_threshold=0.65)

    assert seen == {"n_chunks": 1, "n_qa": 1, "dataset_type": "sample_chunks", "threshold": 0.65}
    assert result["coverage_results"]["coverage_rate"] == 1.0


def test_run_rejects_non_csv_input(monkeypatch, tmp_path):
    """チャンク済み CSV 以外は受け付けない（先に ① チャンキングを通す）。"""
    txt = tmp_path / "raw.txt"
    txt.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(pipeline_mod, "SmartQAGenerator", lambda model: FakeGenerator())

    with pytest.raises(ValueError, match="未対応のファイル形式"):
        QAPipeline(input_file=str(txt), output_dir=str(tmp_path / "out")).run(analyze_coverage=False)


def test_pipeline_no_longer_accepts_dead_arguments(monkeypatch, tmp_path):
    """処理に効いていなかった `client` / `batch_chunks` は 2026-10-09 に削除した。"""
    pipe, _fake, _out = _make_pipeline(monkeypatch, tmp_path, ["alpha"])

    with pytest.raises(TypeError):
        QAPipeline(input_file=pipe.input_file, client=object())
    with pytest.raises(TypeError):
        pipe.run(batch_chunks=3)


# ===================================================================
# SmartQAGenerator.process_chunk()
# ===================================================================


class FakeClient:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc
        self.last_usage = {"input_tokens": 120, "output_tokens": 40}
        self.kwargs = None

    def generate_structured(self, **kwargs):
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return self.result


def _generator(client) -> SmartQAGenerator:
    gen = SmartQAGenerator.__new__(SmartQAGenerator)  # LLM クライアントの生成を避ける
    gen.model = "fake-model"
    gen.client = client
    gen.last_usage = {"input_tokens": 0, "output_tokens": 0}
    return gen


def test_process_chunk_maps_structured_result():
    client = FakeClient(SmartQAResult(
        qa_count=1, key_topics=["価格"], importance_score=0.6, complexity="low", reasoning="r",
        qa_pairs=[SmartQAPair(question="値段は？", answer="3,000円", topic="")],
    ))

    result = _generator(client).process_chunk("価格は3,000円です。")

    assert result["success"] is True
    assert result["analysis"] == {"qa_count": 1, "key_topics": ["価格"], "importance_score": 0.6,
                                  "complexity": "low", "reasoning": "r"}
    # topic が空なら「その他」へ倒す
    assert result["qa_pairs"] == [{"question": "値段は？", "answer": "3,000円", "topic": "その他"}]
    assert result["usage"] == {"input_tokens": 120, "output_tokens": 40}
    # 構造化出力 1 回・スキーマは SmartQAResult・本文がプロンプトに入る
    assert client.kwargs["response_schema"] is SmartQAResult
    assert client.kwargs["model"] == "fake-model"
    assert "価格は3,000円です。" in client.kwargs["prompt"]


@pytest.mark.parametrize("client", [
    FakeClient(exc=RuntimeError("API error")),
    FakeClient(result=None),  # 空応答も失敗扱い
])
def test_process_chunk_returns_failure_instead_of_raising(client):
    result = _generator(client).process_chunk("text")

    assert result == {"analysis": {}, "qa_pairs": [],
                      "usage": {"input_tokens": 0, "output_tokens": 0}, "success": False}


# ===================================================================
# analyze_coverage()
# ===================================================================

_VECTORS = {
    "chunk-A": [1.0, 0.0],
    "chunk-B": [0.0, 1.0],
    "Q-A A-A": [1.0, 0.0],                           # chunk-A と同じ向き（類似度 1.0）
    "Q-B A-B": [np.sqrt(0.42), np.sqrt(0.58)],       # chunk-B と約 0.76（lenient・standard は満たし strict は満たさない）
}


class FakeSemanticCoverage:
    def generate_embeddings(self, doc_chunks):
        return np.array([_VECTORS[c["text"]] for c in doc_chunks])

    def generate_embeddings_batch(self, texts, batch_size=100):
        return np.array([_VECTORS[t] for t in texts]).reshape(len(texts), -1) if texts else np.empty((0, 2))

    def cosine_similarity(self, a, b):
        return float(np.dot(a, b))


class FakeEncoding:
    def encode(self, text):
        return list(text)


@pytest.fixture
def fake_embeddings(monkeypatch):
    monkeypatch.setattr(evaluation, "SemanticCoverage", FakeSemanticCoverage)
    monkeypatch.setattr(evaluation.tiktoken, "get_encoding", lambda name: FakeEncoding())


CHUNKS = [{"id": "a", "text": "chunk-A"}, {"id": "b", "text": "chunk-B"}]


def test_analyze_coverage_counts_chunks_above_threshold(fake_embeddings):
    result = evaluation.analyze_coverage(
        CHUNKS, [{"question": "Q-A", "answer": "A-A"}], dataset_type="sample"
    )

    assert result["threshold"] == 0.7
    assert result["coverage_rate"] == 0.5
    assert result["covered_chunks"] == 1
    assert [u["chunk"]["id"] for u in result["uncovered_chunks"]] == ["b"]
    assert result["dataset_type"] == "sample"
    assert result["optimal_thresholds"] == {"strict": 0.8, "standard": 0.7, "lenient": 0.6}
    assert set(result["multi_threshold"]) == {"strict", "standard", "lenient"}
    assert set(result["chunk_analysis"]) == {"by_length", "by_position", "summary"}


def test_analyze_coverage_multi_threshold_and_custom_threshold(fake_embeddings):
    qa = [{"question": "Q-A", "answer": "A-A"}, {"question": "Q-B", "answer": "A-B"}]

    result = evaluation.analyze_coverage(CHUNKS, qa, dataset_type="sample")
    mt = result["multi_threshold"]
    assert mt["strict"]["covered_chunks"] == 1     # chunk-B（約 0.76）は 0.8 に届かない
    assert mt["standard"]["covered_chunks"] == 2
    assert mt["lenient"]["covered_chunks"] == 2
    assert result["coverage_rate"] == 1.0

    # custom_threshold は standard 閾値だけを上書きする
    strict = evaluation.analyze_coverage(CHUNKS, qa, dataset_type="sample", custom_threshold=0.9)
    assert strict["threshold"] == 0.9
    assert strict["coverage_rate"] == 0.5


def test_analyze_coverage_with_no_qa_returns_zero(fake_embeddings):
    result = evaluation.analyze_coverage(CHUNKS, [], dataset_type="sample")

    assert result["coverage_rate"] == 0.0
    assert result["covered_chunks"] == 0
    assert result["total_chunks"] == 2
