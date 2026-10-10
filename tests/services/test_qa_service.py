from unittest.mock import MagicMock, patch

import services
import services.qa_service as qa_service
from services.qa_service import (
    QAPair,
    generate_qa_pairs,
    save_qa_pairs_to_file,
)


class TestQAService:

    @patch("services.qa_service.create_llm_client")
    def test_generate_qa_pairs(self, mock_create_client):
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client
        
        # Mock structured output
        mock_response = MagicMock()
        mock_qa = MagicMock()
        mock_qa.question = "Q1"
        mock_qa.answer = "A1"
        mock_qa.question_type = "factual"
        
        mock_response.qa_pairs = [mock_qa]
        mock_client.generate_structured.return_value = mock_response
        
        result = generate_qa_pairs("text", "dataset", "chunk1")
        
        assert len(result) == 1
        assert isinstance(result[0], QAPair)
        assert result[0].question == "Q1"
        assert result[0].source_chunk_id == "chunk1"

    @patch("services.qa_service.pd.DataFrame.to_csv")
    @patch("services.qa_service.json.dump")
    @patch("builtins.open")
    @patch("services.qa_service.Path.mkdir")
    def test_save_qa_pairs_to_file(self, mock_mkdir, mock_open_file, mock_json_dump, mock_to_csv):
        qa_pairs = [
            QAPair(question="Q", answer="A", question_type="T", source_chunk_id="C", dataset_type="D")
        ]
        
        result = save_qa_pairs_to_file(qa_pairs, "dataset_type")
        
        assert "csv" in result
        assert "json" in result
        mock_to_csv.assert_called()
        mock_json_dump.assert_called()

    def test_run_advanced_qa_generation_is_removed(self):
        """`run_advanced_qa_generation` を書き戻していないこと。

        存在しない `qa_generator_runner` を import する死にコードだったため 2026-09-25 に削除した。
        Q/A 生成パイプラインの実行口は CLI（`qa_qdrant/make_qa_register_qdrant.py`）と
        `services/data_pipeline_service.py::run_qa_generation_sync()` である。
        """
        assert not hasattr(qa_service, "run_advanced_qa_generation")
        assert "run_advanced_qa_generation" not in services.__all__
