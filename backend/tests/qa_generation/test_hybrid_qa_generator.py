"""`helper/helper_rag_qa.py` の `HybridQAGenerator` が最後まで動くことを固定するテスト。

## なぜ必要か

2026-09-25 まで、`HybridQAGenerator` は定義されていないメソッドを 8 つ呼んでいた
（`extract_entities` / `remove_duplicates` / `identify_uncovered_sections` /
`verify_answer_in_text` / `check_question_clarity` / `check_no_contradiction` /
`check_length_appropriateness` / `calculate_quality_score`）。Phase 2 で使う
`TemplateBasedQAGenerator.find_answer_in_text` も定義が無かった。
呼び出し元が無かったので気付かれず、`generate_comprehensive_qa()` を呼ぶと Phase 2 で必ず
`AttributeError` になっていた。

## どう確かめるか

- 静的検査: 2 クラスの `self.xxx(...)` がすべてクラス内に定義されていること（`ast`）。
- 動作確認: spaCy の `nlp` と LLM を偽物に差し替え、Phase 1〜4 を最後まで通す。

`helper/helper_rag_qa.py` は先頭で `import spacy` する。本リポジトリの CI には spaCy が入っているが、
入っていない環境でも動くよう、無ければ空のモジュールを差し込んで import する
（姉妹リポジトリ grace_v2 の CI は spaCy を入れていないので、同じテストを共有している）。`spacy.load` は呼ばない（`RuleBasedQAGenerator.__init__` を通さない）ので、
spaCy 本体・日本語モデルが無くても動く。実 LLM / Qdrant 不要。
"""

import ast
import importlib
import sys
import types
from pathlib import Path

import pytest

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "helper" / "helper_rag_qa.py").exists())
_SOURCE = _ROOT / "helper" / "helper_rag_qa.py"

TEXT = (
    "富士山とは日本で最も高い山である。"
    "富士山は静岡県と山梨県にまたがっている。"
    "葛飾北斎は富士山を題材に多くの作品を描いた。"
)


@pytest.fixture(scope="module")
def rag_qa():
    """spaCy が無い環境では空のモジュールを差し込んで `helper.helper_rag_qa` を import する。"""
    stubbed = importlib.util.find_spec("spacy") is None
    if stubbed:
        sys.modules["spacy"] = types.ModuleType("spacy")
    try:
        yield importlib.import_module("helper.helper_rag_qa")
    finally:
        if stubbed:
            sys.modules.pop("spacy", None)
            sys.modules.pop("helper.helper_rag_qa", None)


class _FakeNLP:
    """`nlp(text)` の戻り値として `ents` と `sents` だけを持つ偽物。"""

    def __init__(self, ents):
        self._ents = [types.SimpleNamespace(text=t, label_=label) for t, label in ents]

    def __call__(self, text):
        return types.SimpleNamespace(ents=self._ents, sents=[])


class _FakeLLM:
    def __init__(self, qa_pairs):
        self.qa_pairs = qa_pairs
        self.received = None

    def generate_diverse_qa(self, text):
        self.received = text
        return [dict(qa) for qa in self.qa_pairs]


def _make_generator(rag_qa, ents=(), llm_qa=()):
    """`__init__`（spacy.load・LLM クライアント生成）を通さずに組み立てる。"""
    rule = rag_qa.RuleBasedQAGenerator.__new__(rag_qa.RuleBasedQAGenerator)
    rule.nlp = _FakeNLP(ents)
    gen = rag_qa.HybridQAGenerator.__new__(rag_qa.HybridQAGenerator)
    gen.rule_generator = rule
    gen.template_generator = rag_qa.TemplateBasedQAGenerator()
    gen.llm_generator = _FakeLLM(llm_qa)
    return gen


# ---------------------------------------------------------------------------
# 静的検査
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("class_name", ["HybridQAGenerator", "TemplateBasedQAGenerator"])
def test_every_self_method_call_is_defined(class_name):
    """クラス内の `self.xxx(...)` 呼び出しが、すべて同じクラスに定義されていること。"""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"), filename=str(_SOURCE))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    defined = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    called = {
        node.func.attr
        for node in ast.walk(cls)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    }
    assert called - defined == set()


# ---------------------------------------------------------------------------
# パイプライン全体
# ---------------------------------------------------------------------------

def test_generate_comprehensive_qa_runs_all_phases(rag_qa):
    """Phase 1〜4 を最後まで通り、検証を通った Q/A だけが品質スコア順に返ること。"""
    llm_qa = [
        # 原文に根拠があり、既存の問いとも重ならない → 残る
        {"question": "富士山はどの県にまたがっていますか？", "answer": "静岡県と山梨県にまたがっています。"},
        # 原文に無い内容 → answer_found で落ちる
        {"question": "世界で最も高い山はどこにありますか？", "answer": "エベレストはネパールにあります。"},
        # 指示語で始まる → question_clear で落ちる
        {"question": "それは何ですか？", "answer": "日本で最も高い山です。"},
        # ルールベースと同じ問いで回答が違う → no_contradiction で落ちる
        {"question": "富士山とは何ですか？", "answer": "静岡県にある山です。"},
    ]
    gen = _make_generator(
        rag_qa,
        ents=[("富士山", "LOC"), ("葛飾北斎", "PERSON"), ("富士山", "LOC")],
        llm_qa=llm_qa,
    )

    result = gen.generate_comprehensive_qa(TEXT, target_count=50)

    questions = [qa["question"] for qa in result]
    assert "富士山とは何ですか？" in questions                    # Phase 1（ルールベース）
    assert "葛飾北斎は誰ですか？" in questions                    # Phase 2（テンプレート）
    assert "富士山はどの県にまたがっていますか？" in questions      # Phase 3（LLM）
    assert "世界で最も高い山はどこにありますか？" not in questions
    assert "それは何ですか？" not in questions
    assert len(questions) == len(set(questions))

    # 同じ問いに回答が 2 つ残らず、先に採用したルールベースの回答が残る
    fuji = [qa for qa in result if qa["question"] == "富士山とは何ですか？"]
    assert [qa["answer"] for qa in fuji] == ["富士山とは日本で最も高い山です。"]

    for qa in result:
        assert all(qa["validations"].values())
        assert 0.0 <= qa["quality_score"] <= 1.0
    scores = [qa["quality_score"] for qa in result]
    assert scores == sorted(scores, reverse=True)

    # Phase 3 の LLM には原文の一部（空でない）が渡る
    assert gen.llm_generator.received
    assert all(line in TEXT for line in gen.llm_generator.received.split("\n"))


# ---------------------------------------------------------------------------
# 個別メソッド
# ---------------------------------------------------------------------------

def test_extract_entities_dedupes_same_text_and_type(rag_qa):
    gen = _make_generator(rag_qa, ents=[("富士山", "LOC"), ("富士山", "LOC"), ("富士山", "ORG"), (" ", "LOC")])
    assert gen.extract_entities(TEXT) == [
        {"text": "富士山", "type": "LOC"},
        {"text": "富士山", "type": "ORG"},
    ]


def test_remove_duplicates_works_on_japanese_without_spaces(rag_qa):
    """空白の無い日本語でも、ほぼ同じ質問を重複とみなすこと（単語分割に頼らない）。"""
    gen = _make_generator(rag_qa)
    existing = [{"question": "富士山の高さは何メートルですか？"}]
    new = [
        {"question": "富士山の高さは何メートルですか"},   # 句読点だけ違う → 除く
        {"question": "葛飾北斎は誰ですか？"},
        {"question": "葛飾北斎は誰ですか?"},             # new 内の先行要素と同じ → 除く
    ]
    assert gen.remove_duplicates(new, existing) == [{"question": "葛飾北斎は誰ですか？"}]


def test_identify_uncovered_sections(rag_qa):
    gen = _make_generator(rag_qa)
    qa = [{"question": "富士山とは何ですか？", "answer": "富士山とは日本で最も高い山です。"}]
    assert gen.identify_uncovered_sections(TEXT, qa) == (
        "富士山は静岡県と山梨県にまたがっている。\n葛飾北斎は富士山を題材に多くの作品を描いた。"
    )
    # Q/A が無い・すべてカバー済みなら原文をそのまま返す（LLM への入力を空にしない）
    assert gen.identify_uncovered_sections(TEXT, []) == TEXT
    assert gen.identify_uncovered_sections("富士山とは日本で最も高い山である。", qa) == (
        "富士山とは日本で最も高い山である。"
    )


def test_verify_answer_in_text(rag_qa):
    gen = _make_generator(rag_qa)
    assert gen.verify_answer_in_text("富士山とは日本で最も高い山です。", TEXT) is True
    assert gen.verify_answer_in_text("エベレストはネパールにあります。", TEXT) is False
    assert gen.verify_answer_in_text("", TEXT) is False


@pytest.mark.parametrize("question, expected", [
    ("富士山とは何ですか？", True),
    ("富士山の特徴を説明してください。", True),
    ("富士山の高さは何メートルですか", True),
    ("それは何ですか？", False),         # 指示語で始まる
    ("この山の高さは？", False),         # 指示語で始まる
    ("何？", False),                     # 短すぎる
    ("富士山について。", False),         # 疑問・依頼の形で終わらない
    ("", False),
])
def test_check_question_clarity(rag_qa, question, expected):
    gen = _make_generator(rag_qa)
    assert gen.check_question_clarity(question) is expected


def test_check_no_contradiction(rag_qa):
    gen = _make_generator(rag_qa)
    validated = [{"question": "富士山とは何ですか？", "answer": "日本で最も高い山です。"}]
    assert gen.check_no_contradiction({"question": "富士山とは何ですか？", "answer": "静岡県の山です。"}, validated) is False
    assert gen.check_no_contradiction({"question": "富士山とは何ですか?", "answer": "日本で最も高い山です。"}, validated) is False
    assert gen.check_no_contradiction({"question": "葛飾北斎は誰ですか？", "answer": "浮世絵師です。"}, validated) is True
    assert gen.check_no_contradiction({"question": "葛飾北斎は誰ですか？", "answer": "浮世絵師です。"}, []) is True


def test_check_length_appropriateness(rag_qa):
    gen = _make_generator(rag_qa)
    assert gen.check_length_appropriateness({"question": "富士山とは何ですか？", "answer": "山です。"}) is True
    assert gen.check_length_appropriateness({"question": "何？", "answer": "山です。"}) is False
    assert gen.check_length_appropriateness({"question": "富士山とは何ですか？", "answer": "山"}) is False
    assert gen.check_length_appropriateness({"question": "富士山とは何ですか？", "answer": "あ" * 1001}) is False
    assert gen.check_length_appropriateness({"question": "富士山とは何ですか？"}) is False


def test_calculate_quality_score(rag_qa):
    gen = _make_generator(rag_qa)
    grounded = {"answer": "富士山とは日本で最も高い山である。", "confidence": 0.9}
    assert gen.calculate_quality_score(grounded, TEXT) == pytest.approx(0.95)
    # confidence が無ければ既定値、原文に無い回答は低くなる
    llm = {"answer": "エベレストはネパールにあります。"}
    assert gen.calculate_quality_score(llm, TEXT) < gen.calculate_quality_score(grounded, TEXT)
    assert gen.calculate_quality_score({"answer": "", "confidence": 5}, TEXT) == pytest.approx(0.5)


def test_find_answer_in_text(rag_qa):
    tgen = rag_qa.TemplateBasedQAGenerator()
    # 質問の手がかり（エンティティ名以外）と最も重なる文を選ぶ
    assert tgen.find_answer_in_text(TEXT, "富士山", "富士山はどの県にまたがっていますか？") == (
        "富士山は静岡県と山梨県にまたがっている。"
    )
    # 手がかりが無ければ、エンティティを含む最初の文
    assert tgen.find_answer_in_text(TEXT, "富士山", "富士山？") == "富士山とは日本で最も高い山である。"
    assert tgen.find_answer_in_text(TEXT, "エベレスト", "エベレストとは何ですか？") is None
    assert tgen.find_answer_in_text(TEXT, "", "とは何ですか？") is None
