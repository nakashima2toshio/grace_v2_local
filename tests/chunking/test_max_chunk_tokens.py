"""#53 派生: チャンク最大トークン数の強制分割（Embedding入力上限との連携）。

gemini のチャンクは List[str]（anthropic の List[Dict] と異なる）ため、
文字列ベースで検証する。tiktoken が使えない環境では文字数概算に
フォールバックする（_count_tokens 参照）。
"""
from chunking.csv_text_to_chunks_text_csv import (
    _count_tokens,
    _enforce_max_chunk_tokens,
)


class TestMaxChunkTokenEnforcement:
    def test_oversized_chunk_is_split_at_sentence_boundary(self):
        sentence = "これはテスト用の文章でありおおよそ四十文字程度の長さを持つ一文です。"
        big_text = "".join(sentence for _ in range(5))

        result = _enforce_max_chunk_tokens([big_text], max_tokens=100)

        assert len(result) > 1  # 分割された
        # テキストは保全される（文の欠落なし。結合時の空白は除去して比較）
        joined = "".join(c.replace(" ", "") for c in result)
        assert joined == big_text

    def test_within_limit_chunk_is_untouched(self):
        chunks = ["短いチャンク。"]
        assert _enforce_max_chunk_tokens(chunks, max_tokens=512) == chunks

    def test_single_oversized_sentence_kept_whole(self):
        """1文で上限超の場合は文の途中で切らず保持（警告のみ）"""
        one_long_sentence = "あ" * 300 + "。"
        result = _enforce_max_chunk_tokens([one_long_sentence], max_tokens=100)
        assert result == [one_long_sentence]

    def test_split_pieces_respect_token_limit(self):
        sentence = "これは四十文字程度のテスト文章でありそれなりの長さを持っています。"
        big_text = "".join(sentence for _ in range(10))

        result = _enforce_max_chunk_tokens([big_text], max_tokens=120)

        # 各ピースは（分割可能な範囲で）上限内に収まる
        for c in result:
            assert _count_tokens(c) <= 120

    def test_empty_and_mixed_list(self):
        chunks = ["短い。", "あ" * 500 + "。あ" * 5 + "。"]
        result = _enforce_max_chunk_tokens(chunks, max_tokens=50)
        # 先頭の短いチャンクはそのまま残る
        assert result[0] == "短い。"
        assert len(result) >= len(chunks)
