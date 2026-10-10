"""
tests/legacy/ 共通フィクスチャ

legacy テスト群がモジュール再編前に参照していた共通フィクスチャを、
利用箇所から推定して最小実装で復元する。
"""
import pandas as pd
import pytest


@pytest.fixture
def temp_dir(tmp_path):
    """一時ディレクトリ（pathlib.Path）。"""
    return tmp_path


@pytest.fixture
def qa_output_dir(tmp_path):
    """qa_output 系テスト用の一時ディレクトリ。

    test_load_history_with_files はフィクスチャ自体は単なる依存マーカーとして
    使い、実体は CWD 配下の qa_output ディレクトリを自前で作成・削除している。
    """
    d = tmp_path / "qa_output"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def sample_qa_df():
    """question / answer 列を持つ 3 行の Q&A DataFrame。

    legacy テストは row 0 の question に「Pythonとは何ですか？」、
    answer に「Pythonは汎用」を含むことを前提にしている。
    """
    return pd.DataFrame(
        {
            "question": [
                "Pythonとは何ですか？",
                "機械学習とは何ですか？",
                "RAGとは何ですか？",
            ],
            "answer": [
                "Pythonは汎用プログラミング言語です。",
                "機械学習はデータからパターンを学習する手法です。",
                "RAGは検索拡張生成の略です。",
            ],
        }
    )


@pytest.fixture
def sample_text_df():
    """Combined_Text 列を持つテキスト DataFrame。"""
    return pd.DataFrame(
        {
            "Combined_Text": [
                "これはサンプルテキスト1です。",
                "これはサンプルテキスト2です。",
            ]
        }
    )
