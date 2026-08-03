# backend/tests/test_scope_and_models.py
"""担当範囲の明示（W-2）と適合性チェックのモデル選択（M-3）の回帰テスト。

## W-2: スコープ方針の注入

検索スコープ（`VerticalProfile.collections`）が効くのは **内部 RAG だけ**で、
⑤ Web フォールバックと executor の動的 web_search にはドメイン制限が無い
（`WebSearchConfig` に allowed_domains 相当のフィールドが無く、
`WebSearchTool.execute` も query/num_results/language しか受け取らない）。

実測では gov プロファイルで「明日の東京の天気は？」を混ぜると天気サイトが
引用に載った。取得側を絞ると 0 件化 → 情報なし回答 → 誤エスカレを招きやすい
ため、まず生成側（reasoning プロンプト）で担当範囲を明示する。

## M-3: 適合性チェックのモデル

`Executor._evaluate_rag_relevance` は YES / NO の 2 値しか返さないのに
主モデル（`llm.model`）を使っており、実測で 1 回あたり数秒かかったうえ、
十分だった RAG 経路を捨てて Web 検索へ落とす原因になっていた。
軽量モデルを既定にしつつ、`executor.relevance_check_model` で
A/B・巻き戻しができることを固定する。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.core.verticals import PROFILES, SCOPE_POLICY, VerticalProfile
from backend.tests.conftest import PipelineStub, install_pipeline_stub, make_config_stub

# ---------------------------------------------------------------------------
# W-2: build_prompt_addendum の合成
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vertical", sorted(PROFILES))
def test_every_profile_carries_scope_policy(vertical):
    """全プロファイルの注入テキストにスコープ方針が含まれる。"""
    addendum = PROFILES[vertical].build_prompt_addendum()

    assert SCOPE_POLICY in addendum
    # 業界固有の方針も残っていること（置き換えではなく追加）
    assert PROFILES[vertical].prompt_addendum in addendum


def test_scope_policy_allows_partial_answer():
    """複合質問で範囲内の質問まで断らないよう、例外を明文化してある。

    この一文が落ちると「住民票の取り方は？ ところで明日の天気は？」で
    住民票側まで丸ごと断られうる（実測で確認した複合質問ケース）。
    """
    assert "同時に含まれる場合" in SCOPE_POLICY


def test_profile_without_addendum_still_gets_scope_policy():
    """業界固有の方針が空でもスコープ方針だけは注入される。"""
    profile = VerticalProfile(name="テスト", prompt_addendum="")

    assert profile.build_prompt_addendum() == SCOPE_POLICY


def test_prompt_addendum_field_is_not_mutated():
    """`/api/verticals` が返す生のフィールドは汚さない（合成は呼び出し時のみ）。"""
    before = PROFILES["gov"].prompt_addendum
    PROFILES["gov"].build_prompt_addendum()

    assert PROFILES["gov"].prompt_addendum == before
    assert SCOPE_POLICY not in PROFILES["gov"].prompt_addendum


def test_core_injects_scope_policy_into_config(monkeypatch):
    """run_support_agent_core が合成後の方針を config へ配線する。"""
    from backend.app.core import support_agent as core

    stub = PipelineStub(config=make_config_stub())
    install_pipeline_stub(monkeypatch, stub)

    captured: dict = {}
    registry_factory = core.create_tool_registry
    monkeypatch.setattr(
        core, "create_tool_registry",
        lambda cfg: (captured.setdefault("cfg", cfg), registry_factory(cfg))[1],
    )

    core.run_support_agent_core("住民票の写しの取り方は？", vertical="gov")

    addendum = captured["cfg"].llm.prompt_addendum
    assert SCOPE_POLICY in addendum
    assert PROFILES["gov"].prompt_addendum in addendum


def test_core_without_vertical_injects_nothing(monkeypatch):
    """--vertical 未指定は汎用エージェント扱い。スコープ方針を入れない。"""
    from backend.app.core import support_agent as core

    stub = PipelineStub(config=make_config_stub())
    install_pipeline_stub(monkeypatch, stub)

    captured: dict = {}
    registry_factory = core.create_tool_registry
    monkeypatch.setattr(
        core, "create_tool_registry",
        lambda cfg: (captured.setdefault("cfg", cfg), registry_factory(cfg))[1],
    )

    core.run_support_agent_core("パスワードを忘れました", vertical=None)

    assert captured["cfg"].llm.prompt_addendum == ""


# ---------------------------------------------------------------------------
# W-2: ReasoningTool のプロンプトに実際に載るか
# ---------------------------------------------------------------------------


def _reasoning_tool(addendum: str):
    """Qdrant / LLM に触れない ReasoningTool を組み立てる。"""
    from grace.tools import ReasoningTool

    tool = ReasoningTool.__new__(ReasoningTool)  # __init__ のクライアント生成を回避
    tool.config = SimpleNamespace(llm=SimpleNamespace(prompt_addendum=addendum))
    return tool


def test_reasoning_prompt_contains_scope_policy():
    """合成済みの方針が reasoning プロンプトの【業務方針（遵守）】へ載る。"""
    tool = _reasoning_tool(PROFILES["gov"].build_prompt_addendum())

    prompt = tool._build_prompt("住民票の写しの取り方は？", context=None, sources=None)

    assert "業務方針" in prompt
    assert SCOPE_POLICY in prompt


def test_reasoning_prompt_omits_section_when_empty():
    """方針が空なら【業務方針】セクション自体を出さない（既存挙動の不変性）。"""
    tool = _reasoning_tool("")

    prompt = tool._build_prompt("パスワードを忘れました", context=None, sources=None)

    assert "業務方針" not in prompt


# ---------------------------------------------------------------------------
# M-3: 適合性チェックのモデル解決
# ---------------------------------------------------------------------------


def _executor_with(llm=None, executor_cfg=None):
    """設定だけを持つ Executor を組み立てる（__init__ の依存生成を回避）。"""
    from grace.executor import Executor

    ex = Executor.__new__(Executor)
    ex.config = SimpleNamespace(
        llm=llm or SimpleNamespace(
            model="claude-sonnet-4-6", light_model="claude-haiku-4-5-20251001"
        ),
        executor=executor_cfg if executor_cfg is not None
        else SimpleNamespace(relevance_check_model=""),
    )
    return ex


def test_relevance_check_defaults_to_light_model():
    """既定では軽量モデルを使う（YES/NO の 2 値判定に主モデルは過剰）。"""
    assert _executor_with()._relevance_check_model() == "claude-haiku-4-5-20251001"


def test_relevance_check_explicit_override_wins():
    """明示指定があればそれを使う（A/B・従来挙動への巻き戻し）。"""
    ex = _executor_with(
        executor_cfg=SimpleNamespace(relevance_check_model="claude-sonnet-4-6")
    )

    assert ex._relevance_check_model() == "claude-sonnet-4-6"


def test_relevance_check_falls_back_to_main_model():
    """軽量モデル未設定の環境では主モデルへフォールバックする。"""
    ex = _executor_with(
        llm=SimpleNamespace(model="claude-sonnet-4-6", light_model="")
    )

    assert ex._relevance_check_model() == "claude-sonnet-4-6"


def test_relevance_check_tolerates_missing_executor_config():
    """executor セクションを持たない設定でも落ちない（後方互換）。"""
    from grace.executor import Executor

    ex = Executor.__new__(Executor)
    ex.config = SimpleNamespace(
        llm=SimpleNamespace(model="claude-sonnet-4-6", light_model="claude-haiku-4-5-20251001")
    )

    assert ex._relevance_check_model() == "claude-haiku-4-5-20251001"


def test_relevance_check_model_config_default_is_empty():
    """新設フィールドの既定は空（＝light_model へ委譲）。"""
    from grace.config import ExecutorConfig

    assert ExecutorConfig().relevance_check_model == ""


# ---------------------------------------------------------------------------
# M-3: 呼び出し箇所が解決結果を実際に使うか
#
# 上の解決ロジック単体テストだけでは、`_evaluate_rag_relevance` が
# `self.config.llm.model` を直接参照するコードへ巻き戻っても検出できない。
# 実際に LLM へ渡された model を捕まえて固定する。
# ---------------------------------------------------------------------------


def _install_llm_spy(monkeypatch, captured: dict, text: str = "YES"):
    """`create_chat_client` を差し替え、generate_content の引数を記録する。"""

    def fake_generate_content(*, model, contents, config=None, **kwargs):
        captured["model"] = model
        captured["contents"] = contents
        return SimpleNamespace(text=text)

    client = SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate_content))
    monkeypatch.setattr("grace.executor.create_chat_client", lambda _cfg: client)


def test_evaluate_rag_relevance_uses_light_model(monkeypatch):
    """既定では軽量モデルで判定する（主モデルを引かない）。"""
    captured: dict = {}
    _install_llm_spy(monkeypatch, captured)

    ex = _executor_with()
    assert ex._evaluate_rag_relevance("住民票の写しの取り方は？", "住民票は…") is True
    assert captured["model"] == "claude-haiku-4-5-20251001"


def test_evaluate_rag_relevance_honors_override(monkeypatch):
    """明示指定があれば呼び出し側もそれを使う（巻き戻し経路の担保）。"""
    captured: dict = {}
    _install_llm_spy(monkeypatch, captured)

    ex = _executor_with(
        executor_cfg=SimpleNamespace(relevance_check_model="claude-sonnet-4-6")
    )
    ex._evaluate_rag_relevance("住民票の写しの取り方は？", "住民票は…")

    assert captured["model"] == "claude-sonnet-4-6"


def test_evaluate_rag_relevance_no_defaults_to_false(monkeypatch):
    """NO 応答は不適合（既存挙動の不変性）。"""
    captured: dict = {}
    _install_llm_spy(monkeypatch, captured, text="NO")

    assert _executor_with()._evaluate_rag_relevance("天気は？", "住民票は…") is False


def test_evaluate_rag_relevance_empty_answer_defaults_to_true(monkeypatch):
    """空応答は判定不能 → 適合扱い（スコアのみの従来判定を維持）。"""
    captured: dict = {}
    _install_llm_spy(monkeypatch, captured, text="")

    assert _executor_with()._evaluate_rag_relevance("住民票は？", "住民票は…") is True


def test_evaluate_rag_relevance_exception_defaults_to_true(monkeypatch):
    """LLM 失敗時も適合扱いでフォールバックする（既存挙動の不変性）。"""

    def boom(_cfg):
        raise RuntimeError("LLM down")

    monkeypatch.setattr("grace.executor.create_chat_client", boom)

    assert _executor_with()._evaluate_rag_relevance("住民票は？", "住民票は…") is True


# ---------------------------------------------------------------------------
# M-5: 適合性チェックのプロンプト
#
# 旧プロンプトは「【検索結果】の主題が【ユーザーの質問】の主題と一致するか」を
# 結合クエリ全体に対して問うていた。gov プロファイルで
# 「住民票の写しの取り方は？ ところで明日の東京の天気は？」を投げると、
# 住民票だけを含む検索結果は必ず NO になり、担当範囲外の天気を埋めるためだけに
# Web 検索が走っていた（実測: 約 4 秒 + SerpAPI 1 回 + 無関係な引用 9 件）。
#
# 誤判定しても RAG 結果は捨てられない（web_search は「追加」実行で、
# 両方が reasoning へ渡る）ので、失うのは回答ではなく時間・コスト・精度。
# ---------------------------------------------------------------------------


def _relevance_prompt(monkeypatch, query: str, rag_output, addendum: str = "") -> str:
    """`_evaluate_rag_relevance` が組み立てたプロンプトを取り出す。"""
    captured: dict = {}
    _install_llm_spy(monkeypatch, captured)

    ex = _executor_with(
        llm=SimpleNamespace(
            model="claude-sonnet-4-6",
            light_model="claude-haiku-4-5-20251001",
            prompt_addendum=addendum,
        )
    )
    ex._evaluate_rag_relevance(query, rag_output)
    return captured["contents"]


def test_relevance_prompt_carries_scope_when_profile_set(monkeypatch):
    """業界プロファイルがあれば担当範囲を判定材料として渡す。"""
    prompt = _relevance_prompt(
        monkeypatch,
        "住民票の写しの取り方は？ ところで明日の東京の天気は？",
        "住民票は市区町村の窓口で…",
        addendum=PROFILES["gov"].build_prompt_addendum(),
    )

    assert "【担当範囲】" in prompt
    assert SCOPE_POLICY in prompt
    # 担当範囲外の事項を判定から除外する指示が入っていること
    assert "担当範囲外の事項が含まれる場合" in prompt


def test_relevance_prompt_omits_scope_when_generic(monkeypatch):
    """--vertical 未指定（汎用）では担当範囲ブロックを出さない。"""
    prompt = _relevance_prompt(monkeypatch, "住民票の写しの取り方は？", "住民票は…")

    assert "【担当範囲】" not in prompt
    assert "担当範囲外の事項が含まれる場合" not in prompt


def test_relevance_prompt_judges_each_topic(monkeypatch):
    """複合質問は事項ごとに判定させる（結合クエリ一括判定からの脱却）。

    旧プロンプトの「検索結果の主題が質問の主題と一致しているか」が
    復活すると、住民票だけの検索結果が再び一律 NO になる。
    """
    prompt = _relevance_prompt(monkeypatch, "AとBは？", "Aは…")

    assert "事項ごとに判定する" in prompt
    assert "検索結果の主題が質問の主題と一致しているか" not in prompt


def test_relevance_prompt_uses_formatted_snippet(monkeypatch):
    """検索結果は整形済みの本文で渡す（dict の repr を垂れ流さない）。"""
    prompt = _relevance_prompt(
        monkeypatch,
        "住民票の写しの取り方は？",
        [{"payload": {"question": "住民票の取り方", "answer": "窓口で申請します"}}],
    )

    assert "1. 住民票の取り方 / 窓口で申請します" in prompt
    assert "'payload'" not in prompt


# --- _format_rag_snippet 単体 ---------------------------------------------


def _snippet(rag_output, **kw) -> str:
    from grace.executor import Executor

    return Executor._format_rag_snippet(rag_output, **kw)


def test_format_rag_snippet_bounds_by_characters_not_elements():
    """リストは **文字数** で切る。

    旧実装は `rag_output[:500]` で、リストに対しては「先頭 500 **要素**」の
    スライスになっていた。RAG は dict のリストを返すため、実際には
    ほぼ全件がプロンプトへ流れ込んでいた。
    """
    hits = [{"payload": {"content": "あ" * 200}} for _ in range(50)]

    out = _snippet(hits, limit=300)

    assert len(out) <= 300


def test_format_rag_snippet_falls_back_to_content():
    """answer が無ければ content を使う（チャンク由来のペイロード）。"""
    out = _snippet([{"payload": {"question": "Q1", "content": "本文です"}}])

    assert out == "1. Q1 / 本文です"


def test_format_rag_snippet_keeps_string_input():
    """文字列で渡ってきた場合は従来どおり先頭を切るだけ。"""
    assert _snippet("あ" * 50, limit=10) == "あ" * 10


def test_format_rag_snippet_handles_unknown_shape():
    """dict でも list でもない出力でも落ちない（後方互換）。"""
    assert _snippet({"foo": "bar"}, limit=100).startswith("{'foo'")


def test_format_rag_snippet_handles_payloadless_items():
    """payload を持たない要素は repr へフォールバックする。"""
    out = _snippet(["生テキスト"])

    assert out == "1. 生テキスト"
