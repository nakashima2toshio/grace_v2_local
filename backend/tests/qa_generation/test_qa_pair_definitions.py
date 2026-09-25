"""`QAPair` の定義場所を固定するテスト。

2026-09-25 に `QAPair` をリポジトリ直下 `models.py` の定義へ一本化した。

| 場所 | 状態 |
|---|---|
| `models.py`（リポジトリ直下） | **正本**。`services/qa_service.py` が使う |
| `qa_generation/models.py` | 自前の定義を削除し、正本を import して再エクスポートする |
| `helper/helper_rag_qa.py` | 統合元の旧定義が残る（別物。`difficulty` / `source_span` を持つ） |

以前は `qa_generation/models.py` にも別定義（`difficulty` / `source_span`）があり、
`from models import QAPair` と `from qa_generation import QAPair` が**別のクラス**を指していた。
Pydantic は知らない項目名を黙って無視するため、取り違えると `difficulty="hard"` などの値が
エラーも出ずに消えていた。このテストは、`qa_generation` 側に別定義が戻っていないことを確かめる。

`helper/helper_rag_qa.py` は `spacy`（と MeCab 系の `regex_mecab`）を import するため、それらが無い
環境でも落ちないよう、旧定義だけは `ast` でソースを読んで確かめる（grace_v2 と同じテスト）。
実 LLM / Qdrant 不要。
"""

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _fields(model_cls) -> set:
    return set(model_cls.model_fields.keys())


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


def test_legacy_definition_is_still_separate():
    """`helper/helper_rag_qa.py` の旧定義は別物として残っていること（フィールドが違う）。"""
    from models import QAPair as TopLevelQAPair

    classes = _classes_named(_ROOT / "helper" / "helper_rag_qa.py", "QAPair")
    assert len(classes) == 1
    legacy = {
        stmt.target.id
        for stmt in classes[0].body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    }
    assert legacy == {"question", "answer", "question_type", "difficulty", "source_span"}
    assert "difficulty" not in _fields(TopLevelQAPair)
    assert "difficulty_level" in _fields(TopLevelQAPair)
