"""`qa_qdrant/make_qa_register_qdrant.py` の `.txt` 入力を固定するテスト。

2026-09-26 まで、`--input-file *.txt` は `QAPipeline` へそのまま渡され、
`QAPipeline.load_data()` が `.csv` しか受け付けないため**必ず失敗していた**
（docstring は「テキストファイルから チャンク作成 + Q/A 生成 + 登録」と説明していた）。
現在は、先にチャンク化 CLI・データ管理タブと同じ `run_chunking_sync()` で
`<--chunk-output>/<入力名>_chunks.csv` を作り、それを Q/A 生成に渡す
（経緯は `qa_qdrant/docs/make_qa_register_qdrant_ipo.md` §3.3 の 1）。

チャンク化・Ollama の確認・Q/A 生成・Qdrant 登録はすべて差し替えるので、
実 LLM / Qdrant / API キーは不要。
"""

import sys
from pathlib import Path

import pytest

import qa_qdrant.make_qa_register_qdrant as mqr


class _FakePipeline:
    """`QAPipeline` の代わり。受け取った入力を記録し、Q/A CSV を 1 つ書いて返す。"""

    instances = []

    def __init__(self, dataset_name=None, input_file=None, model=None, output_dir=None,
                 max_docs=None, text_column=None):
        self.input_file = input_file
        self.model = model
        self.output_dir = output_dir
        _FakePipeline.instances.append(self)

    def run(self, **kwargs):
        out = Path(self.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        qa_csv = out / "qa_pairs_doc_chunks_20260926_101500.csv"
        qa_csv.write_text("question,answer\nQ,A\n", encoding="utf-8")
        return {"saved_files": {"qa_csv": str(qa_csv)}, "qa_count": 1}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """チャンク化・Ollama の確認・Q/A 生成・登録を差し替え、呼び出しを記録する。"""
    import services.data_pipeline_service as dps

    calls = {"chunking": [], "registration": [], "pulled_check": []}

    def fake_chunking(text, **kwargs):
        calls["chunking"].append({"text": text, **kwargs})
        Path(kwargs["output_file"]).write_text("text\nチャンク\n", encoding="utf-8")
        return ["チャンク"]

    def fake_registration(**kwargs):
        calls["registration"].append(kwargs)
        return True

    def fake_not_pulled(model):
        calls["pulled_check"].append(model)
        return None

    _FakePipeline.instances = []
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    monkeypatch.setattr(dps, "run_chunking_sync", fake_chunking)
    monkeypatch.setattr(dps, "ollama_unreachable_message", lambda *a, **k: None)
    monkeypatch.setattr(dps, "model_not_pulled_message", fake_not_pulled)
    monkeypatch.setattr(mqr, "QAPipeline", _FakePipeline)
    monkeypatch.setattr(mqr, "run_registration", fake_registration)
    return calls


def _argv(monkeypatch, tmp_path, input_file, *extra):
    monkeypatch.setattr(sys, "argv", [
        "make_qa_register_qdrant.py",
        "--input-file", str(input_file),
        "--collection", "tmp_collection",
        "--output", str(tmp_path / "qa"),
        "--ui-output", str(tmp_path / "ui"),
        "--chunk-output", str(tmp_path / "chunks"),
        *extra,
    ])


def test_txt_is_chunked_then_passed_to_qa_generation(monkeypatch, tmp_path, env):
    """`.txt` は先にチャンク化され、できたチャンク CSV が Q/A 生成・登録へ流れること。"""
    doc = tmp_path / "doc.txt"
    doc.write_text("富士山は日本で最も高い山である。", encoding="utf-8")
    _argv(monkeypatch, tmp_path, doc, "--chunk-model", "chunk-model:1b", "--model", "qa-model:1b")

    mqr.main()

    expected_chunks = tmp_path / "chunks" / "doc_chunks.csv"
    assert len(env["chunking"]) == 1
    chunk_call = env["chunking"][0]
    assert chunk_call["text"] == "富士山は日本で最も高い山である。"
    assert Path(chunk_call["output_file"]) == expected_chunks
    assert chunk_call["model"] == "chunk-model:1b"
    assert chunk_call["dataset_type"] == "doc"
    assert chunk_call["source_file"] == "doc.txt"

    # チャンク化用・Q/A 生成用の両方のモデルが pull 済みか確かめてから進む
    assert env["pulled_check"] == ["chunk-model:1b", "qa-model:1b"]

    assert [Path(p.input_file) for p in _FakePipeline.instances] == [expected_chunks]
    assert len(env["registration"]) == 1
    assert env["registration"][0]["csv_path"].endswith("qa_pairs_doc_chunks_20260926_101500.csv")


def test_empty_txt_exits_with_1(monkeypatch, tmp_path, env):
    """空の `.txt` はチャンク化を呼ばずに終了コード 1 で止まること。"""
    doc = tmp_path / "empty.txt"
    doc.write_text("  \n", encoding="utf-8")
    _argv(monkeypatch, tmp_path, doc)

    with pytest.raises(SystemExit) as exc:
        mqr.main()
    assert exc.value.code == 1
    assert env["chunking"] == []
    assert env["registration"] == []


def test_txt_with_ollama_down_exits_with_1(monkeypatch, tmp_path, env):
    """チャンク化に要る Ollama に繋がらなければ、チャンク化前に終了コード 1 で止まること。"""
    import services.data_pipeline_service as dps

    monkeypatch.setattr(dps, "ollama_unreachable_message", lambda *a, **k: "❌ Ollama に接続できません")
    doc = tmp_path / "doc.txt"
    doc.write_text("本文", encoding="utf-8")
    _argv(monkeypatch, tmp_path, doc)

    with pytest.raises(SystemExit) as exc:
        mqr.main()
    assert exc.value.code == 1
    assert env["chunking"] == []
