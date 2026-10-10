"""#53: QAPipeline のチャンク単位 JSONL 逐次永続化・クラッシュ再開のテスト。

実 LLM/Celery 不要。SmartQAGenerator をモックして同期経路（_generate_sync）を駆動する。
"""
import json
import os
import tempfile
from unittest.mock import patch

import pandas as pd


class TestPipelinePersistence:
    """逐次永続化・再開のテスト"""

    def _make_pipeline(self, tmpdir):
        from qa_generation.pipeline import QAPipeline

        csv_path = os.path.join(tmpdir, "in_chunks.csv")
        pd.DataFrame({
            "chunk_id": ["c1", "c2", "c3"],
            "text": ["text1", "text2", "text3"],
        }).to_csv(csv_path, index=False)

        with patch("qa_generation.pipeline.SmartQAGenerator") as MockGen:
            inst = MockGen.return_value
            inst.process_chunk.return_value = {
                "success": True,
                "qa_pairs": [{"question": "Q", "answer": "A", "topic": "T"}],
                "analysis": {},
            }
            pipeline = QAPipeline(input_file=csv_path, output_dir=tmpdir)
            pipeline.smart_generator = inst
        return pipeline, inst

    def test_progress_written_per_chunk(self):
        with tempfile.TemporaryDirectory() as td:
            pipeline, _ = self._make_pipeline(td)
            chunks = pipeline._load_chunks_from_csv(pipeline.load_data())

            pairs = pipeline.generate_qa(chunks, use_celery=False)

            assert len(pairs) == 3
            progress_path = pipeline._progress_path()
            assert progress_path.exists()
            lines = progress_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 3
            assert all("chunk_id" in json.loads(line) for line in lines)

    def test_resume_skips_processed_chunks(self):
        with tempfile.TemporaryDirectory() as td:
            pipeline, inst = self._make_pipeline(td)
            chunks = pipeline._load_chunks_from_csv(pipeline.load_data())

            # 2チャンク処理済み（うち1つは qa_count=0）の状態を作る
            progress_path = pipeline._progress_path()
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            with open(progress_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"chunk_id": "c1",
                                    "qa_pairs": [{"question": "Q1", "answer": "A1"}]}) + "\n")
                f.write(json.dumps({"chunk_id": "c2", "qa_pairs": []}) + "\n")

            pairs = pipeline.generate_qa(chunks, use_celery=False)

            # 未処理の c3 のみ処理される
            assert inst.process_chunk.call_count == 1
            # 復元1件（c1）+ 新規1件（c3）。c2 は qa_count=0 として再処理されない
            assert len(pairs) == 2

    def test_corrupted_progress_line_skipped(self):
        """壊れた行（途中クラッシュ）はスキップされ、そのチャンクは再処理される"""
        with tempfile.TemporaryDirectory() as td:
            pipeline, inst = self._make_pipeline(td)
            chunks = pipeline._load_chunks_from_csv(pipeline.load_data())

            progress_path = pipeline._progress_path()
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            with open(progress_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"chunk_id": "c1", "qa_pairs": []}) + "\n")
                f.write('{"chunk_id": "c2", "qa_pa')  # 壊れた行

            pipeline.generate_qa(chunks, use_celery=False)

            # c1 はスキップ、c2（壊れた行）と c3 は処理される
            assert inst.process_chunk.call_count == 2

    def test_clear_progress(self):
        with tempfile.TemporaryDirectory() as td:
            pipeline, _ = self._make_pipeline(td)
            pipeline._append_progress("c1", [])
            assert pipeline._progress_path().exists()
            pipeline._clear_progress()
            assert not pipeline._progress_path().exists()
