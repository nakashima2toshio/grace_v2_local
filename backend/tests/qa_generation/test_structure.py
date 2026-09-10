from helper.helper_text import merge_small_chunks


def test_merge_small_chunks_logic():
    # 現行の merge_small_chunks は各チャンクが事前計算済みの
    # tokens / doc_id / chunk_idx を持つことを前提にしている。
    # current["tokens"] < min_tokens かつ統合後 <= max_tokens のとき
    # 同一 doc_id のチャンクを貪欲に統合する。
    chunks = [
        {"id": "1", "text": "Short text.", "doc_id": "d1", "chunk_idx": 0, "tokens": 10},
        {"id": "2", "text": "Another short text.", "doc_id": "d1", "chunk_idx": 1, "tokens": 45},
        {"id": "3", "text": "A very long text", "doc_id": "d1", "chunk_idx": 2, "tokens": 200},
    ]

    merged = merge_small_chunks(chunks, min_tokens=50, max_tokens=500)

    # 1 + 2 を統合すると tokens=55 (>= min_tokens=50) になるため
    # 以降の 3 とは統合されず、結果は 2 チャンクになる。
    assert len(merged) == 2
    assert "Short text." in merged[0]["text"]
    assert "Another short text." in merged[0]["text"]
    assert merged[0]["tokens"] == 55
    assert merged[1]["id"] == "3"


def test_merge_different_docs():
    chunks = [
        {"id": "1", "text": "Short.", "doc_id": "d1", "chunk_idx": 0, "tokens": 5},
        {"id": "2", "text": "Short.", "doc_id": "d2", "chunk_idx": 0, "tokens": 5},
    ]
    # doc_id が異なるため統合されない
    merged = merge_small_chunks(chunks, min_tokens=50, max_tokens=500)
    assert len(merged) == 2
