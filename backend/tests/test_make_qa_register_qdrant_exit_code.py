"""`qa_qdrant/make_qa_register_qdrant.py` の終了コードを固定するテスト。

2026-09-26 まで、次の 2 つの失敗が**終了コード 0** で終わっていた。シェルスクリプトや
ジョブ管理から失敗を検知できないため、どちらも終了コード 1 で止めるよう直した
（経緯は `qa_qdrant/docs/make_qa_register_qdrant_ipo.md` §3.3 の 2・6）。

- Phase 2（Qdrant 登録）の `run_registration()` が `False` を返しても、`main()` は
  エラーログを出すだけだった
- Phase 1 で Q/A が 0 件でも（Ollama が落ちている等）、空の Q/A CSV のまま登録へ進み、
  登録が失敗しても上と同じく 0 で終わっていた

`question` / `answer` 列を持つ CSV を渡すと Phase 1（Q/A 生成）を飛ばすので、
`run_registration` をモックに差し替えれば実 LLM / Qdrant / API キーなしで `main()` を通せる。
"""

import sys
from pathlib import Path

import pytest

import qa_qdrant.make_qa_register_qdrant as mqr


@pytest.fixture
def qa_csv(tmp_path):
    path = tmp_path / "qa.csv"
    path.write_text("question,answer\n富士山の高さは？,3776メートル\n", encoding="utf-8")
    return path


def _run_main(monkeypatch, qa_csv, tmp_path, registration_result):
    calls = []

    def fake_registration(**kwargs):
        calls.append(kwargs)
        return registration_result

    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    monkeypatch.setattr(mqr, "run_registration", fake_registration)
    monkeypatch.setattr(sys, "argv", [
        "make_qa_register_qdrant.py",
        "--input-file", str(qa_csv),
        "--collection", "tmp_collection",
        "--ui-output", str(tmp_path / "ui"),
    ])
    mqr.main()
    return calls


def test_registration_failure_exits_with_1(monkeypatch, qa_csv, tmp_path):
    """Qdrant 登録が失敗したら終了コード 1 で止まること。"""
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, qa_csv, tmp_path, registration_result=False)
    assert exc.value.code == 1


def test_registration_success_returns_normally(monkeypatch, qa_csv, tmp_path):
    """登録が成功したら例外なく戻る（終了コード 0）。Q/A 済み CSV はそのまま登録に渡る。"""
    calls = _run_main(monkeypatch, qa_csv, tmp_path, registration_result=True)
    assert len(calls) == 1
    assert calls[0]["csv_path"] == str(qa_csv)
    assert calls[0]["collection_name"] == "tmp_collection"


class _EmptyPipeline:
    """Q/A を 1 件も作れなかった `QAPipeline`（Ollama 停止時の実際の結果と同じ形）。"""

    def __init__(self, **kwargs):
        self.output_dir = kwargs["output_dir"]

    def run(self, **kwargs):
        out = Path(self.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        # 実装（qa_generation/data_io.py::save_results）は 0 件でも空の CSV を書く
        qa_csv = out / "qa_pairs_chunks_20260926_101500.csv"
        qa_csv.write_text("", encoding="utf-8")
        return {"saved_files": {"qa_csv": str(qa_csv)}, "qa_count": 0}


def test_zero_qa_pairs_exits_with_1_before_registration(monkeypatch, tmp_path):
    """Q/A が 0 件なら、登録へ進まずに終了コード 1 で止まること。"""
    import services.data_pipeline_service as dps

    csv = tmp_path / "chunks.csv"
    csv.write_text("text\n富士山は日本で最も高い山である。\n", encoding="utf-8")
    registrations = []

    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    monkeypatch.setattr(dps, "ollama_unreachable_message", lambda *a, **k: None)
    monkeypatch.setattr(dps, "model_not_pulled_message", lambda *a, **k: None)
    monkeypatch.setattr(mqr, "QAPipeline", _EmptyPipeline)
    monkeypatch.setattr(mqr, "run_registration", lambda **kw: registrations.append(kw) or True)
    monkeypatch.setattr(sys, "argv", [
        "make_qa_register_qdrant.py",
        "--input-file", str(csv),
        "--collection", "tmp_collection",
        "--output", str(tmp_path / "qa"),
        "--ui-output", str(tmp_path / "ui"),
    ])

    with pytest.raises(SystemExit) as exc:
        mqr.main()
    assert exc.value.code == 1
    assert registrations == []
