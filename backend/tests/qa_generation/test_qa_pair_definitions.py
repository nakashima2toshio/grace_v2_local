"""`QAPair` / `QAPairsList` の定義場所を固定するテスト。

2026-09-25 に `QAPair` と `QAPairsList` をリポジトリ直下 `models.py` の定義へ一本化した
（`QAPairsList` は `models.QAPairsResponse` の別名。`qa_generation/models.py` と
`helper/helper_rag_qa.py` にあった同名クラスは削除し、どちらも `from models import ...` する）。

| 場所 | 状態 |
|---|---|
| `models.py`（リポジトリ直下） | **正本**。`services/qa_service.py` が使う |
| `qa_generation/models.py` | 自前の定義を削除し、正本を import して再エクスポートする |
| `helper/helper_rag_qa.py` | 旧定義（`difficulty` / `source_span`）を削除し、正本を import して使う |

以前は `qa_generation/models.py` と `helper/helper_rag_qa.py` にも別定義（`difficulty` / `source_span`）があり、
`from models import QAPair` と `from qa_generation import QAPair` が**別のクラス**を指していた。
Pydantic は知らない項目名を黙って無視するため、取り違えると `difficulty="hard"` などの値が
エラーも出ずに消えていた。このテストは、どちらにも別定義が戻っていないことを確かめる。

`helper/helper_rag_qa.py` は `spacy`（と MeCab 系の `regex_mecab`）を import するため、それらが無い
環境でも落ちないよう、このファイルだけは `ast` でソースを読んで確かめる（grace_v2 と同じテスト）。
実 LLM / Qdrant 不要。
"""

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _classes_named(path: Path, name: str) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name]


def test_package_qa_pair_is_the_top_level_definition():
    """`qa_generation` 側の `QAPair` は直下 `models.py` のクラスそのものであること。"""
    import qa_generation
    from models import QAPair as TopLevelQAPair
    from qa_generation.models import QAPair as PackageQAPair

    assert PackageQAPair is TopLevelQAPair
    assert qa_generation.QAPair is TopLevelQAPair
    assert "QAPair" in qa_generation.__all__


def test_qa_generation_models_does_not_define_its_own_qa_pair():
    """`qa_generation/models.py` に `class QAPair` を書き戻していないこと（静的検査）。"""
    assert _classes_named(_ROOT / "qa_generation" / "models.py", "QAPair") == []


def test_package_qa_pairs_list_holds_the_top_level_qa_pair():
    """`qa_generation.QAPairsList` の要素型も正本の `QAPair` であること。"""
    from models import QAPair as TopLevelQAPair
    from qa_generation.models import QAPairsList

    lst = QAPairsList(qa_pairs=[{"question": "Q", "answer": "A", "difficulty_level": "hard"}])
    assert isinstance(lst.qa_pairs[0], TopLevelQAPair)
    assert lst.qa_pairs[0].difficulty_level == "hard"


def test_helper_rag_qa_uses_the_top_level_definition():
    """`helper/helper_rag_qa.py` は旧定義を持たず、直下 `models.py` の `QAPair` を import すること（静的検査）。"""
    path = _ROOT / "helper" / "helper_rag_qa.py"
    assert _classes_named(path, "QAPair") == []

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "models" and node.level == 0
        and any(alias.name == "QAPair" and alias.asname is None for alias in node.names)
    ]
    assert len(imports) == 1


def test_qa_pairs_list_is_a_single_definition():
    """`QAPairsList` はどこから import しても直下 `models.QAPairsResponse` そのものであること。"""
    import models
    import qa_generation
    from qa_generation.models import QAPairsList as PackageQAPairsList

    assert models.QAPairsList is models.QAPairsResponse
    assert PackageQAPairsList is models.QAPairsResponse
    assert qa_generation.QAPairsList is models.QAPairsResponse
    assert "QAPairsList" in qa_generation.__all__


def test_no_module_defines_its_own_qa_pairs_list():
    """`qa_generation/models.py` と `helper/helper_rag_qa.py` に `class QAPairsList` を書き戻していないこと。

    `helper/helper_rag_qa.py` は `spacy` を import するので `ast` で読み、
    `from models import ... QAPairsList` していることも確かめる。
    """
    assert _classes_named(_ROOT / "qa_generation" / "models.py", "QAPairsList") == []

    path = _ROOT / "helper" / "helper_rag_qa.py"
    assert _classes_named(path, "QAPairsList") == []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "models" and node.level == 0
        for alias in node.names
    }
    assert "QAPairsList" in imported
