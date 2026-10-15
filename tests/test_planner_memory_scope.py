# tests/test_planner_memory_scope.py
"""Planner が rag_search に付けるコレクションと、LLM 計画の後処理の回帰テスト。

## 背景（実測 2026-10-15・`tests/usage/usage_planner.py`）

「Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？」
（cc_news のニュース記事にある話題）に対して、

    [memory] prioritized collection for query: ec_policy_anthropic

が出ていた。EC の返品・領収書ポリシーが、49ers の質問の rag_search に
**先頭で**付く。RAGSearchTool は一次しきい値を超えた最初のコレクションで
打ち切るので、そこで当たればニュースは検索すらされない。

### ① 重なりが無いと全体集計へ倒れていた

`collection_priors` はキーワードの重なるレコードが無いと**全レコードの集計**へ
フォールバックしていた。全体の首位（実績の多い ec_policy）が、どんな新しい
質問にも返る。「この種の質問で当たりやすいコレクション」という推定の意味が無い。

### ② キーワード抽出が文節を丸ごと 1 語にしていた

`[一-鿿゠-ヿ぀-ゟ]{2,}` は漢字・かな・カナの連続を 1 語にするので、
「住民票の写しの取り方は」が 1 語になる。言い回しが少し違うだけで重ならず、
① のフォールバックへ落ちる。

### ③ LLM 計画のプロンプトに、検索されないコレクションが並んでいた

`_get_available_collections` が Qdrant の全件を返していたため、除外リスト
（cc_news / wikipedia / fineweb …）や次元の違うコレクション（*_ollama / *_768 …）
まで LLM に見せていた。LLM が名指しすると `PlanStep.collection` に入り、
RAGSearchTool からは「明示指定」に見えて除外を素通りする。
同じ一覧を使うフォールバック計画は、名前に "wikipedia" を含むものを
**除外リストに載っていても**先頭に固定していた。

### ④ LLM 計画の created_at が null、timeout_seconds が設定を無視

LLM が `"created_at": null` を返すと `default_factory` が効かず null のまま。
`timeout_seconds` も LLM の言い値（60）のままで、`planner.step_timeout_seconds`
（30）が効かなかった。ルールベース計画とリプランは設定値を使う。

⚠️ LLM・Qdrant には接続しない。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from grace.config import GraceConfig
from grace.memory import ExecutionMemory, extract_keywords
from grace.planner import Planner
from grace.schemas import ExecutionPlan
from grace.tools import RAGSearchTool

NEWS_QUERY = "Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？"


def _write_memory(tmp_path, rows):
    """旧形式（文節を丸ごと keywords に持つ）のレコードを JSONL へ書く。"""
    path = tmp_path / "grace_memory.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for query, keywords, collection, n in rows:
            for i in range(n):
                f.write(json.dumps({
                    "query": query, "keywords": keywords, "collection": collection,
                    "success": True, "confidence": 0.95, "timestamp": float(i),
                }, ensure_ascii=False) + "\n")
    return ExecutionMemory(str(path))


# 実ファイル（logs/grace_memory.jsonl）と同じ形: EC が全体の首位
REAL_LIKE_ROWS = [
    ("領収書は発行できますか？", ["領収書は発行できますか"], "ec_policy_anthropic", 8),
    ("返品したい", ["返品したい"], "ec_policy_anthropic", 8),
    ("住民票の写しの取り方は？", ["住民票の写しの取り方は"], "gov_faq_anthropic", 4),
]


# =============================================================================
# ① ② 実行メモリ
# =============================================================================

class TestMemoryPrior:

    def test_unrelated_query_gets_no_prior(self, tmp_path):
        """実測の再現: ニュースの質問に EC ポリシーを付けない（None ＝ 全件検索）。"""
        memory = _write_memory(tmp_path, REAL_LIKE_ROWS)

        assert memory.best_collection(query=NEWS_QUERY, min_count=3, min_score=0.6) is None

    def test_unrelated_query_has_empty_priors(self, tmp_path):
        memory = _write_memory(tmp_path, REAL_LIKE_ROWS)

        assert memory.collection_priors(query=NEWS_QUERY) == []

    def test_paraphrase_hits_the_same_collection(self, tmp_path):
        """言い回しが違っても「住民票」で重なる。全体首位の EC には倒れない。"""
        memory = _write_memory(tmp_path, REAL_LIKE_ROWS)

        assert memory.best_collection(
            query="住民票の写しを取りたい", min_count=3, min_score=0.6,
        ) == "gov_faq_anthropic"

    def test_old_records_are_matched_by_their_query_text(self, tmp_path):
        """記録済みの keywords（旧形式）ではなく query から抽出し直して照合する。

        旧形式の keywords（"住民票の写しの取り方は"）は新しい抽出結果（"住民票"）と
        一致しないので、keywords で照合すると既存の実績が全部使えなくなる。
        """
        memory = _write_memory(tmp_path, REAL_LIKE_ROWS)

        stats = memory.collection_priors(query="住民票について")

        assert [s.collection for s in stats] == ["gov_faq_anthropic"]
        assert stats[0].count == 4

    def test_no_query_still_aggregates_everything(self, tmp_path):
        """query を渡さない呼び出し（全体の分布を見たいとき）は従来どおり。"""
        memory = _write_memory(tmp_path, REAL_LIKE_ROWS)

        assert {s.collection for s in memory.collection_priors()} == {
            "ec_policy_anthropic", "gov_faq_anthropic",
        }


class TestExtractKeywords:

    def test_hiragana_is_a_separator_not_part_of_a_word(self):
        assert extract_keywords("住民票の写しの取り方は") == ["住民票"]
        assert extract_keywords("明日の東京の天気は？") == ["明日", "東京", "天気"]

    def test_mixed_scripts_split_into_words(self):
        assert extract_keywords(NEWS_QUERY) == ["jed", "york", "ceo", "gm", "コーチ", "背景", "経緯"]

    def test_full_width_is_normalized(self):
        """全角英数・半角カナは NFKC で揃える（同じ語が別の語にならない）。"""
        assert extract_keywords("ＧＭ　２０２６年") == ["gm", "2026"]
        assert extract_keywords("ｻｰﾋﾞｽ") == ["サービス"]


# =============================================================================
# ③ LLM 計画のプロンプトに出すコレクション
# =============================================================================

EXCLUDED = ["cc_news", "fineweb", "wikipedia", "livedoor", "japanese_text"]

ALL = [
    "gov_faq_anthropic", "cc_news_2per_anthropic", "wikipedia_ja_5per",
    "ec_policy_anthropic", "fineweb_edu_ja_5per",
]


class _FakeQdrant:
    """`get_collections()` / `count()` だけを持つ偽の Qdrant クライアント（全件 1 点ずつ）。"""

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=c) for c in ALL])

    def count(self, _name, exact=False):
        return SimpleNamespace(count=1)


def _planner(monkeypatch, allowed=None):
    """Qdrant/LLM に触れない Planner。

    grace_v2_local では除外リストを `_get_all_collections_dynamic(apply_exclusions=...)` の
    中で当てるので、この関数は差し替えず、Qdrant クライアントだけを偽物にして本物を通す
    （次元は不明＝採用、全件に実体あり）。
    """
    monkeypatch.setattr(RAGSearchTool, "_VALID_COLLECTIONS_CACHE", {})
    monkeypatch.setattr(RAGSearchTool, "client", property(lambda _s: _FakeQdrant()))
    monkeypatch.setattr(RAGSearchTool, "_collection_dense_dim", lambda _s, _name: None)
    # 旧実装（Planner が Qdrant の全件を直接取っていた）でも同じ一覧が返るようにする。
    # これが無いと、Qdrant の無い環境では旧実装も空の一覧になり、回帰を捕まえない。
    monkeypatch.setattr("grace.planner.QdrantClient", lambda **_kw: None, raising=False)
    monkeypatch.setattr(
        "grace.planner.get_all_collections", lambda _c: [{"name": c} for c in ALL],
        raising=False,
    )
    config = GraceConfig()
    # 除外リストは運用で変わる値（config/grace_config.yml で調整する）なので、既定値に頼らず明示する
    config.qdrant.excluded_collections = list(EXCLUDED)
    if allowed is not None:
        config.qdrant.allowed_collections = list(allowed)
    planner = Planner.__new__(Planner)
    planner.config = config
    planner._memory = None
    return planner


class TestPromptCollections:

    def test_excluded_collections_are_not_shown(self, monkeypatch):
        assert _planner(monkeypatch)._get_available_collections() == [
            "gov_faq_anthropic", "ec_policy_anthropic",
        ]

    def test_profile_allowed_collections_are_shown_even_if_excluded(self, monkeypatch):
        """業界プロファイルが名指しした汎用コーパスは、RAGSearchTool と同じく残す。"""
        planner = _planner(monkeypatch, allowed=["gov_faq_anthropic", "wikipedia_ja"])

        assert planner._get_available_collections() == [
            "gov_faq_anthropic", "wikipedia_ja_5per",
        ]

    def test_prompt_lists_only_searchable_collections(self, monkeypatch):
        prompt = _planner(monkeypatch)._build_plan_prompt(NEWS_QUERY)

        assert "gov_faq_anthropic, ec_policy_anthropic" in prompt
        assert "cc_news_2per_anthropic" not in prompt

    def test_fallback_plan_does_not_pin_an_excluded_collection(self, monkeypatch):
        """実行メモリに実績が無ければ None（＝ RAGSearchTool が全件を順に検索）。"""
        plan = _planner(monkeypatch)._create_fallback_plan(NEWS_QUERY)

        assert plan.steps[0].collection is None

    def test_qdrant_down_gives_an_empty_list(self, monkeypatch):
        # grace_v2_local は Qdrant に繋がらないとき空のリストを返す
        monkeypatch.setattr(
            RAGSearchTool, "_get_all_collections_dynamic", lambda _s, apply_exclusions=True: [],
        )
        planner = Planner.__new__(Planner)
        planner.config = GraceConfig()

        assert planner._get_available_collections() == []


# =============================================================================
# ④ LLM 計画の後処理
# =============================================================================

def _llm_plan_json(timeout=60):
    """実測（2026-10-15）の LLM 出力と同じ形。created_at・plan_id が null。"""
    return json.dumps({
        "original_query": "最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて",
        "complexity": 0.3, "estimated_steps": 2, "requires_confirmation": False,
        "steps": [
            {"step_id": 1, "action": "web_search", "description": "Web 検索",
             "query": "49ers GM", "collection": None, "depends_on": [],
             "expected_output": "記事", "fallback": "rag_search", "timeout_seconds": timeout},
            {"step_id": 2, "action": "reasoning", "description": "回答",
             "query": None, "collection": None, "depends_on": [1],
             "expected_output": "回答", "fallback": None, "timeout_seconds": timeout},
        ],
        "success_criteria": "回答できている", "created_at": None, "plan_id": None,
    }, ensure_ascii=False)


def _bare_planner():
    planner = Planner.__new__(Planner)
    planner.config = GraceConfig()
    planner._memory = None
    return planner


class TestFinalizeLlmPlan:

    def test_created_at_is_set_when_llm_returns_null(self):
        plan = ExecutionPlan.model_validate_json(_llm_plan_json())
        assert plan.created_at is None  # 前提: default_factory は効かない

        assert _bare_planner()._finalize_plan(plan, 0.4).created_at is not None

    def test_step_timeouts_follow_the_config(self):
        planner = _bare_planner()
        plan = planner._finalize_plan(ExecutionPlan.model_validate_json(_llm_plan_json(60)), 0.4)

        assert [s.timeout_seconds for s in plan.steps] == [
            planner.config.planner.step_timeout_seconds,
        ] * 2

    def test_refine_plan_gets_the_same_treatment(self):
        planner = _bare_planner()
        planner._generate_plan_with_retry = (
            lambda *_a, **_kw: ExecutionPlan.model_validate_json(_llm_plan_json(60))
        )
        original = ExecutionPlan.model_validate_json(_llm_plan_json())

        refined = planner.refine_plan(original, "もっと短く")

        assert refined.created_at is not None
        assert refined.plan_id
        assert {s.timeout_seconds for s in refined.steps} == {
            planner.config.planner.step_timeout_seconds,
        }


def test_planner_config_stub_shape_is_real():
    """`_planner` が使う設定項目が実在する（偽装した設定でテストしていない）。"""
    config = GraceConfig()
    assert isinstance(config.qdrant.excluded_collections, list)
    assert isinstance(config.planner.step_timeout_seconds, int)
