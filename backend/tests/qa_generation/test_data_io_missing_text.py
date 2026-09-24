"""`qa_generation.data_io.load_uploaded_file()` が欠損セルを文字列 "nan" にしないことを検証する。

## なぜ必要か

`Combined_Text` が無い入力では、候補列（`text` / `content` …）から `Combined_Text` を作る。
以前は `clean_text(str(x))` と**先に `str()` をかけていた**ため、pandas の欠損値（`NaN`）が
`clean_text()` の欠損判定に届かず、文字列 `"nan"` として残っていた。
`"nan"` は空白ではないので空行の除外もすり抜け、**`"nan"` から Q/A が生成されうる**
（2026-09-24 に実測: `text` 列に空セル → `['hello', 'nan', 'world']`）。

候補列が 1 つも無いときの全列連結も同じ理由で `"nan"` を連結していた。

経緯は `qa_generation/docs/data_io.md` §8 の 7。grace_v2 と同じ修正（両リポジトリの `data_io.py` は同一だった）。
"""

from qa_generation.data_io import load_uploaded_file


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_candidate_column_empty_cell_is_dropped(tmp_path):
    """候補列の空セルは "nan" にならず、行ごと除外される。"""
    path = _write(tmp_path, "t.csv", "text,id\nhello,1\n,2\nworld,3\n")

    df = load_uploaded_file(path)

    assert df["Combined_Text"].tolist() == ["hello", "world"]
    assert list(df.index) == [0, 1]


def test_fallback_join_skips_missing_values(tmp_path):
    """候補列が無いときの全列連結も、欠損値を "nan" として連結しない。"""
    path = _write(tmp_path, "t.csv", "title,memo\nA,x\nB,\n,\n")

    df = load_uploaded_file(path)

    # 全セルが欠損の 3 行目は空文字になり、空行として除外される
    assert df["Combined_Text"].tolist() == ["A x", "B"]


def test_candidate_column_non_string_values_are_kept(tmp_path):
    """数値など文字列でない値は、従来どおり文字列化して残る。"""
    path = _write(tmp_path, "t.csv", "text\n123\n4.5\n")

    df = load_uploaded_file(path)

    assert df["Combined_Text"].tolist() == ["123.0", "4.5"]
