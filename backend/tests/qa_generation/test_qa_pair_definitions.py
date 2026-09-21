"""同名 `QAPair` が 3 箇所にある事実を固定するテスト。

このリポジトリには `QAPair` という名前のクラスが 3 組あり、フィールドが違う。

| 定義場所 | 使われ方 |
|---|---|
| `models.py`（リポジトリ直下） | **現役**。`services/qa_service.py` が使う |
| `qa_generation/models.py` | `qa_generation/__init__.py` の再エクスポートのみ |
| `helper/helper_rag_qa.py` | 統合元として残る旧定義 |

「短く書けるから」と import 文を差し替えると、フィールドが合わずに壊れる。
どちらかのフィールドを増減させたときにこのテストが落ちるので、
**気づかないまま片側だけ変わる**ことを防げる。

実 LLM / Qdrant 不要。
"""


def _fields(model_cls) -> set:
    return set(model_cls.model_fields.keys())


class TestQAPairDefinitions:
    """3 つの QAPair は別物である"""

    def test_top_level_and_package_qa_pair_are_distinct_classes(self):
        from models import QAPair as TopLevelQAPair
        from qa_generation.models import QAPair as PackageQAPair

        assert TopLevelQAPair is not PackageQAPair

    def test_field_sets_differ(self):
        """片方にしか無いフィールドがあること（＝入れ替えると壊れること）。"""
        from models import QAPair as TopLevelQAPair
        from qa_generation.models import QAPair as PackageQAPair

        top, pkg = _fields(TopLevelQAPair), _fields(PackageQAPair)

        # 共通の土台
        assert {"question", "answer", "question_type"} <= top
        assert {"question", "answer", "question_type"} <= pkg

        # 片側にしか無いもの（入れ替え検知の要）
        assert "difficulty_level" in top and "difficulty_level" not in pkg
        assert "source_span" in pkg and "source_span" not in top
        assert "difficulty" in pkg and "difficulty" not in top

    def test_legacy_definition_still_exists(self):
        """`helper/helper_rag_qa.py` の旧定義も残っていること。"""
        from helper.helper_rag_qa import QAPair as LegacyQAPair
        from models import QAPair as TopLevelQAPair
        from qa_generation.models import QAPair as PackageQAPair

        assert LegacyQAPair is not TopLevelQAPair
        assert LegacyQAPair is not PackageQAPair

    def test_package_qa_pair_is_reexported_as_the_public_api(self):
        """`qa_generation` の公開 API は package 側の定義を指す。"""
        import qa_generation
        from qa_generation.models import QAPair as PackageQAPair

        assert qa_generation.QAPair is PackageQAPair
        assert "QAPair" in qa_generation.__all__
