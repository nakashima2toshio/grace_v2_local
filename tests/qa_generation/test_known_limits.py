# tests/qa_generation/test_known_limits.py
"""Q/A 生成の失敗や取り違えを、黙って成功に見せないこと（2026-10-11 に見つけた 5 件）。

## なぜこのテストがあるか（`qa_generation/docs/README_qa_generation.md` §5 の 3〜7）

3. **LLM が全チャンクで失敗しても `run()` が `success=True`・`qa_count=0` で終わっていた。**
   CLI（`make_qa_register_qdrant.py`）は 0 件の Q/A CSV を登録へ渡していた。全滅なら例外にする。
   一部だけ失敗したときは `failed_chunks` で返し、逐次保存ファイルを残して、再実行で失敗分だけ作り直す。
4. **Embedding が失敗するとゼロベクトルのままカバレージを数え、「0%」が黙って出ていた。**
   ゼロベクトルを見つけたらカバレージを計算せずに止める。
5. **逐次保存ファイルが入力の中身を照合していなかった。** 名前（`qa_progress_<種別>.jsonl`）と
   `chunk_id` だけで再開するので、同じ名前で中身の違う CSV を流すと前回の Q/A が使われ、
   今回の入力に無いチャンクの Q/A まで混ざっていた。本文のハッシュを記録して照合する。
6. **本文の列が見つからない CSV は、全列（ID・見出しを含む）をつないだ文で Q/A を作っていた。**
   列が無ければ `ValueError` にする（`text_column` で指定できる）。
7. **`import qa_generation` でルートロガーが INFO になっていた**（`smart_qa_generator.py` の
   モジュール先頭の `logging.basicConfig()`）。呼び出し側のログ設定が後から効かなくなる。

**実 LLM・Embedding・ネットワークは使わない**（生成器・Embedding はスタブ）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

import qa_generation.evaluation as evaluation
import qa_generation.pipeline as pipeline_mod
from qa_generation.data_io import load_uploaded_file
from qa_generation.pipeline import QAPipeline

ROOT = Path(__file__).resolve().parents[2]


class FakeGenerator:
    """`SmartQAGenerator` の代わり。`fail_on` に入れた本文だけ失敗（`success=False`）を返す。"""

    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.calls: List[str] = []

    def process_chunk(self, chunk_text: str) -> Dict:
        self.calls.append(chunk_text)
        if chunk_text in self.fail_on:
            return {"analysis": {}, "qa_pairs": [],
                    "usage": {"input_tokens": 0, "output_tokens": 0}, "success": False}
        return {"analysis": {"qa_count": 1},
                "qa_pairs": [{"question": f"Q:{chunk_text}", "answer": f"A:{chunk_text}", "topic": "t"}],
                "usage": {"input_tokens": 1, "output_tokens": 1}, "success": True}


def _pipeline(monkeypatch, tmp_path, texts, fake, ids=None, **kw):
    csv = tmp_path / "doc_chunks.csv"
    ids = ids or [f"c{i}" for i in range(len(texts))]
    pd.DataFrame({"chunk_id": ids, "text": texts}).to_csv(csv, index=False)
    monkeypatch.setattr(pipeline_mod, "SmartQAGenerator", lambda model: fake)
    out = tmp_path / "out"
    return QAPipeline(input_file=str(csv), output_dir=str(out), **kw), out


def _progress(out: Path) -> List[Dict]:
    path = out / "qa_progress_doc_chunks.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# =============================================================================
# 3. 全滅は例外・一部の失敗は failed_chunks と逐次保存ファイルで残す
# =============================================================================


def test_3_全チャンクで失敗したら例外にする(monkeypatch, tmp_path):
    fake = FakeGenerator(fail_on={"一。", "二。"})
    pipe, out = _pipeline(monkeypatch, tmp_path, ["一。", "二。"], fake)

    with pytest.raises(RuntimeError, match="全 2 チャンク"):
        pipe.run(analyze_coverage=False)
    assert not list(out.glob("qa_pairs_*.csv"))     # 0 件の Q/A CSV を書かない


def test_3_一部が失敗したら失敗したチャンクを返し_再実行で失敗分だけ作り直す(monkeypatch, tmp_path):
    fake = FakeGenerator(fail_on={"二。"})
    pipe, out = _pipeline(monkeypatch, tmp_path, ["一。", "二。", "三。"], fake)

    result = pipe.run(analyze_coverage=False)
    assert result["success"] is True
    assert result["qa_count"] == 2
    assert result["failed_chunks"] == ["c1"]
    # 逐次保存ファイルを残す（成功した 2 チャンクだけが入っている）
    assert [r["chunk_id"] for r in _progress(out)] == ["c0", "c2"]

    # 2 回目: LLM が直れば、失敗した c1 だけを作り直し、全件そろった出力を書いて逐次保存ファイルを消す
    fake.fail_on.clear()
    fake.calls.clear()
    result = pipe.run(analyze_coverage=False)
    assert fake.calls == ["二。"]
    assert result["qa_count"] == 3
    assert result["failed_chunks"] == []
    assert _progress(out) == []


def test_3_失敗が無ければfailed_chunksは空(monkeypatch, tmp_path):
    pipe, out = _pipeline(monkeypatch, tmp_path, ["一。"], FakeGenerator())
    result = pipe.run(analyze_coverage=False)
    assert result["failed_chunks"] == []
    assert _progress(out) == []


# =============================================================================
# 4. Embedding のゼロベクトルでカバレージを数えない
# =============================================================================


class _FakeCoverage:
    def __init__(self, zero_rows=()):
        self.zero_rows = set(zero_rows)

    def generate_embeddings(self, chunks):
        out = np.ones((len(chunks), 4)) / 2.0
        for i in self.zero_rows:
            out[i] = 0.0
        return out

    def generate_embeddings_batch(self, texts, batch_size=100):
        return np.ones((len(texts), 4)) / 2.0


@pytest.mark.parametrize("zero_rows", [(0, 1), (1,)])
def test_4_Embeddingがゼロベクトルならカバレージを計算しない(monkeypatch, zero_rows):
    monkeypatch.setattr(evaluation, "SemanticCoverage", lambda: _FakeCoverage(zero_rows))
    chunks = [{"id": "c0", "text": "一。"}, {"id": "c1", "text": "二。"}]
    qa = [{"question": "Q", "answer": "A"}]
    with pytest.raises(RuntimeError, match="Embedding"):
        evaluation.analyze_coverage(chunks, qa, dataset_type="doc")


# =============================================================================
# 5. 逐次保存ファイルは本文を照合する
# =============================================================================


def test_5_前回と本文が違うチャンクは作り直す(monkeypatch, tmp_path):
    fake = FakeGenerator()
    pipe, out = _pipeline(monkeypatch, tmp_path, ["古い本文。", "二。"], fake)
    pipe.generate_qa(pipe._load_chunks_from_csv(pipe.load_data()))   # 保存前に落ちたとする

    # 同じ入力名・同じ chunk_id で、c0 の本文だけ変わった CSV を流す
    fake.calls.clear()
    pipe2, _ = _pipeline(monkeypatch, tmp_path, ["新しい本文。", "二。"], fake)
    result = pipe2.run(analyze_coverage=False)

    assert fake.calls == ["新しい本文。"]               # c1 は本文が同じなので再利用
    qa = json.loads(Path(result["saved_files"]["qa_json"]).read_text(encoding="utf-8"))
    assert [q["answer"] for q in qa] == ["A:二。", "A:新しい本文。"]     # 復元分 → 今回の生成分の順


def test_5_今回の入力に無いチャンクのQAを混ぜない(monkeypatch, tmp_path):
    fake = FakeGenerator()
    pipe, _ = _pipeline(monkeypatch, tmp_path, ["一。", "二。", "三。"], fake)
    pipe.generate_qa(pipe._load_chunks_from_csv(pipe.load_data()))

    pipe2, _ = _pipeline(monkeypatch, tmp_path, ["一。"], fake)     # c1・c2 が無い入力
    result = pipe2.run(analyze_coverage=False)
    assert result["qa_count"] == 1


def test_5_本文ハッシュの無い古い記録はchunk_idで再開する(monkeypatch, tmp_path):
    """照合を入れる前に書かれた逐次保存ファイルは、照合できないので従来どおり使う（警告を出す）。"""
    fake = FakeGenerator()
    pipe, out = _pipeline(monkeypatch, tmp_path, ["一。", "二。"], fake)
    out.mkdir(parents=True)
    (out / "qa_progress_doc_chunks.jsonl").write_text(
        json.dumps({"chunk_id": "c0", "qa_pairs": [{"question": "Q:old", "answer": "A:old", "chunk_id": "c0"}]},
                   ensure_ascii=False) + "\n", encoding="utf-8")

    result = pipe.run(analyze_coverage=False)
    assert fake.calls == ["二。"]
    assert result["qa_count"] == 2


# =============================================================================
# 6. 本文の列が無ければ止める
# =============================================================================


def test_6_本文の列が無いCSVはValueError(tmp_path):
    csv = tmp_path / "faq.csv"
    pd.DataFrame({"chunk_id": ["f0"], "title": ["受付時間"], "本文": ["9時から17時です。"]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="本文の列"):
        load_uploaded_file(str(csv))


def test_6_text_columnで指定すればその列を使う(tmp_path):
    csv = tmp_path / "faq.csv"
    pd.DataFrame({"chunk_id": ["f0", "f1"], "title": ["a", "b"], "本文": ["9時から17時です。", None]}).to_csv(csv, index=False)
    df = load_uploaded_file(str(csv), text_column="本文")
    assert df["Combined_Text"].tolist() == ["9時から17時です。"]     # 空セルの行は落とす


def test_6_text_columnの列が無ければValueError(tmp_path):
    csv = tmp_path / "faq.csv"
    pd.DataFrame({"text": ["一。"]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="本文"):
        load_uploaded_file(str(csv), text_column="本文")


def test_6_chunk_text列も本文として読む(tmp_path):
    """QAPipeline が本文として読む列（`chunk_text`）は、読み込みでも本文として扱う。"""
    csv = tmp_path / "c.csv"
    pd.DataFrame({"chunk_id": ["c0"], "chunk_text": ["一。"]}).to_csv(csv, index=False)
    assert load_uploaded_file(str(csv))["Combined_Text"].tolist() == ["一。"]


def test_6_パイプラインからもtext_columnが読み込みへ渡る(monkeypatch, tmp_path):
    csv = tmp_path / "faq_chunks.csv"
    pd.DataFrame({"chunk_id": ["f0"], "title": ["受付時間"], "本文": ["9時から17時です。"]}).to_csv(csv, index=False)
    fake = FakeGenerator()
    monkeypatch.setattr(pipeline_mod, "SmartQAGenerator", lambda model: fake)

    with pytest.raises(ValueError):
        QAPipeline(input_file=str(csv), output_dir=str(tmp_path / "a")).run(analyze_coverage=False)
    assert fake.calls == []                                             # LLM を呼ぶ前に止まる

    QAPipeline(input_file=str(csv), output_dir=str(tmp_path / "b"), text_column="本文").run(analyze_coverage=False)
    assert fake.calls == ["9時から17時です。"]


# =============================================================================
# 7. import でルートロガーを触らない
# =============================================================================


def test_7_importでルートロガーの設定を変えない():
    code = (
        "import logging; import qa_generation, qa_generation.smart_qa_generator; "
        "r = logging.getLogger(); print(r.level, len(r.handlers))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={"PYTHONPATH": str(ROOT), "PATH": ""}, check=True)
    assert out.stdout.split() == ["30", "0"]                           # WARNING・ハンドラ無しのまま
