# backend/tests/test_levers.py
"""性能レバー M-1 / M-6 / P-06 / W-1 の回帰テスト。

いずれも「既定では現行挙動を変えず、設定で有効化できる」ことを固定する。
既定値そのものを変えた P-06 だけは、変更後の値を明示的に固定する。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# P-06: 出典件数
# ---------------------------------------------------------------------------


def test_rag_search_limit_raised_to_five():
    """出典を 3 → 5 へ。groundedness の判定可能 claim を増やす狙い。"""
    from config import AgentConfig

    assert AgentConfig.RAG_SEARCH_LIMIT == 5


def test_rag_search_limit_stays_within_candidate_pool():
    """候補取得数（20件）を超えない。超えると絞り込みが無意味になる。"""
    from config import AgentConfig

    assert AgentConfig.RAG_SEARCH_LIMIT <= 20


def test_select_by_similarity_honors_larger_limit():
    """限界値を上げた分だけ実際に出典が増える（純関数側の担保）。"""
    from agent_tools import select_by_similarity

    candidates = [{"score": 0.9 - i * 0.01} for i in range(20)]

    picked, _ = select_by_similarity(candidates, 5)

    assert len(picked) == 5
    # score 降順
    assert [c["score"] for c in picked] == sorted(
        (c["score"] for c in picked), reverse=True
    )


# ---------------------------------------------------------------------------
# M-6: 判定率による支持率の減衰
# ---------------------------------------------------------------------------


def _gres(supported: int, contradicted: int, total: int, rate: float | None = None):
    decided = supported + contradicted
    return SimpleNamespace(
        supported=supported,
        contradicted=contradicted,
        total=total,
        support_rate=rate if rate is not None else (supported / decided if decided else 0.0),
    )


def _cc(strength: float = 0.3, target: float = 0.8):
    return SimpleNamespace(
        groundedness_coverage_strength=strength,
        groundedness_coverage_target=target,
    )


def _damp(gres, cc):
    from grace.executor import Executor

    return Executor._damp_support_rate(gres, cc)


def test_damping_penalizes_many_neutral_claims():
    """実測ケース: 11 claim 中 7 しか判定できず、その 7 が全部 supported。

    従来は support_rate=1.0 で「根拠が見つからなかった 4 件」がスコアに出ず、
    overall 0.92 が出ていた。
    """
    damped = _damp(_gres(supported=7, contradicted=0, total=11), _cc())

    assert damped < 1.0
    # (7/11)/0.8 = 0.795 → 1.0 * (0.7 + 0.3*0.795) = 0.9386
    assert damped == pytest.approx(0.9386, abs=1e-3)


def test_damping_is_stronger_when_almost_nothing_is_decided():
    """判定率が極端に低いほど大きく割り引く（1/12 の実測ケース）。"""
    damped = _damp(_gres(supported=1, contradicted=0, total=12), _cc())

    # (1/12)/0.8 = 0.104 → 0.7 + 0.3*0.104 = 0.7313
    assert damped == pytest.approx(0.7313, abs=1e-3)


def test_no_damping_when_coverage_meets_target():
    """判定率が target 以上なら減衰しない（健全な回答を罰しない）。"""
    gres = _gres(supported=8, contradicted=0, total=10)  # 0.8 == target

    assert _damp(gres, _cc()) == pytest.approx(gres.support_rate)


def test_damping_disabled_by_zero_strength():
    """strength=0 で従来どおり（巻き戻し経路）。"""
    gres = _gres(supported=7, contradicted=0, total=11)

    assert _damp(gres, _cc(strength=0.0)) == gres.support_rate


def test_damping_preserves_zero_support_rate():
    """支持率 0 は 0 のまま（減衰は乗算なので符号を変えない）。"""
    gres = _gres(supported=0, contradicted=3, total=11)

    assert _damp(gres, _cc()) == 0.0


def test_damping_skips_when_nothing_decided():
    """判定 0 件は呼び出し側で別扱い。ここでは素通しする。"""
    gres = _gres(supported=0, contradicted=0, total=5)

    assert _damp(gres, _cc()) == gres.support_rate


def test_damping_tolerates_missing_config_fields():
    """旧 config（フィールド無し）でも落ちず、減衰もしない。"""
    gres = _gres(supported=7, contradicted=0, total=11)

    assert _damp(gres, SimpleNamespace()) == gres.support_rate


def test_damping_config_defaults():
    """既定は有効（strength=0.3 / target=0.8）。"""
    from grace.config import ConfidenceConfig

    cfg = ConfidenceConfig()
    assert cfg.groundedness_coverage_strength == 0.3
    assert cfg.groundedness_coverage_target == 0.8


# ---------------------------------------------------------------------------
# M-1: 論理層のモデル解決
# ---------------------------------------------------------------------------


def _cfg(**llm):
    from grace.config import GraceConfig, LLMConfig

    return GraceConfig(llm=LLMConfig(**llm))


def test_heavy_model_defaults_to_main_model():
    """既定（heavy_model 未設定）は現行挙動と同じモデル。"""
    from grace.config import resolve_heavy_model

    assert resolve_heavy_model(_cfg()) == "claude-sonnet-4-6"


def test_heavy_model_override_wins():
    """設定すれば論理層だけ上位モデルへ切り替わる。"""
    from grace.config import resolve_heavy_model

    assert resolve_heavy_model(_cfg(heavy_model="claude-opus-5")) == "claude-opus-5"


def test_thinking_budget_ignored_without_heavy_model():
    """モデルを上げていないのに思考コストだけ増やさない。"""
    from grace.config import heavy_thinking_budget

    assert heavy_thinking_budget(_cfg(heavy_thinking_budget_tokens=4000)) == 0


def test_thinking_budget_active_with_heavy_model():
    from grace.config import heavy_thinking_budget

    cfg = _cfg(heavy_model="claude-opus-5", heavy_thinking_budget_tokens=4000)

    assert heavy_thinking_budget(cfg) == 4000


@pytest.mark.parametrize("cls_path,attr", [
    ("grace.planner.Planner", "model_name"),
    ("grace.tools.ReasoningTool", "model_name"),
    ("grace.confidence.GroundednessVerifier", "model_name"),
])
def test_logic_tier_components_use_heavy_model(monkeypatch, cls_path, attr):
    """計画生成・推論・根拠検証が論理層のモデルを引く。"""
    import importlib

    module_name, class_name = cls_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "create_chat_client", lambda _cfg: object())

    cls = getattr(module, class_name)
    instance = cls(config=_cfg(heavy_model="claude-opus-5"))

    assert getattr(instance, attr) == "claude-opus-5"


# --- llm_compat: thinking の明示制御 ---------------------------------------


class _SpyMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text="ok")], usage=None)


def _call(config: dict | None):
    """Anthropic クライアントを差し替えて messages.create の引数を捕まえる。"""
    from grace.llm_compat import AnthropicGenaiClient

    client = AnthropicGenaiClient(default_model="claude-sonnet-4-6")
    spy = _SpyMessages()
    client._client = SimpleNamespace(messages=spy)

    client.models.generate_content(contents="q", config=config)
    return spy.kwargs


def test_thinking_disabled_explicitly_by_default():
    """既定で thinking を明示 disabled にする。

    これが無いと、拡張思考が既定 ON のモデル（claude-opus-5 等）へ
    差し替えた瞬間に `max_output_tokens: 10` の呼び出し（複雑度推定・
    意図分類・情報なし判定）で本文が空になる。
    """
    kwargs = _call({"max_output_tokens": 10, "temperature": 0.0})

    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["max_tokens"] == 10
    assert kwargs["temperature"] == 0.0


def test_thinking_enabled_widens_max_tokens():
    """思考を有効にしたら本文の取り分を確保する（max_tokens > budget）。"""
    kwargs = _call({"max_output_tokens": 1024, "thinking_budget_tokens": 4000})

    assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4000}
    assert kwargs["max_tokens"] > 4000


def test_thinking_enabled_drops_temperature():
    """拡張思考中は温度を指定できない（API エラーになる）。"""
    kwargs = _call({"temperature": 0.0, "thinking_budget_tokens": 4000})

    assert "temperature" not in kwargs


def test_thinking_budget_raised_to_api_minimum():
    """API 下限（1024）を下回る budget は引き上げる。"""
    kwargs = _call({"max_output_tokens": 2048, "thinking_budget_tokens": 100})

    assert kwargs["thinking"]["budget_tokens"] == 1024


@pytest.mark.parametrize("value", [0, None, "", "abc"])
def test_thinking_budget_falsy_means_disabled(value):
    kwargs = _call({"max_output_tokens": 512, "thinking_budget_tokens": value})

    assert kwargs["thinking"] == {"type": "disabled"}


# ---------------------------------------------------------------------------
# W-1: Web 検索の優先ドメイン
# ---------------------------------------------------------------------------


def _web_tool(domains, boost: float = 0.15):
    """ネットワークに触れない WebSearchTool を組み立てる。"""
    from grace.tools import WebSearchTool

    tool = WebSearchTool.__new__(WebSearchTool)
    tool.config = SimpleNamespace(
        web_search=SimpleNamespace(
            preferred_domains=domains, preferred_domain_boost=boost
        )
    )
    return tool


def _entry(source: str, score: float):
    return {"score": score, "payload": {"source": source}, "collection": "web_search"}


def test_preferred_domains_reorder_without_dropping():
    """優先ドメインを上位へ。**非一致も落とさない**。

    取得側を絞ると 0 件化 → 情報なし回答 → ④' の誤エスカレへ連鎖するため、
    このレバーは順位付けだけを変える。
    """
    results = [
        _entry("https://tenki.example.com/a", 1.0),
        _entry("https://www.city.example.lg.jp/b", 0.9),
        _entry("https://blog.example.com/c", 0.8),
    ]

    out = _web_tool(["go.jp", "lg.jp"])._prefer_domains(results)

    assert len(out) == 3                                    # 件数は減らない
    assert out[0]["payload"]["source"].endswith("/b")       # 優先ドメインが先頭
    assert {e["payload"]["source"] for e in out} == {
        r["payload"]["source"] for r in results
    }


def test_preferred_domains_match_by_host_suffix():
    """接尾辞一致（go.jp は www.city.example.go.jp に一致）。"""
    out = _web_tool(["go.jp"])._prefer_domains([
        _entry("https://www.city.example.go.jp/x", 0.5),
        _entry("https://example.com/y", 0.5),
    ])

    assert out[0]["preferred_domain"] is True
    assert out[1]["preferred_domain"] is False


def test_preferred_domains_do_not_match_substring():
    """部分文字列では一致させない（notgo.jp を go.jp 扱いしない）。"""
    out = _web_tool(["go.jp"])._prefer_domains([_entry("https://notgo.jp/x", 0.5)])

    assert out[0]["preferred_domain"] is False


def test_preferred_domains_noop_when_unset():
    """未設定なら並べ替えも加点も一切しない（既定＝現行挙動）。"""
    results = [_entry("https://b.example.com", 0.5), _entry("https://a.example.com", 0.9)]

    out = _web_tool([])._prefer_domains(list(results))

    assert out == results  # 順序そのまま


def test_preferred_domains_score_capped_at_one():
    """加点はスコアの上限 1.0 を超えない。"""
    out = _web_tool(["go.jp"], boost=0.5)._prefer_domains([_entry("https://a.go.jp", 1.0)])

    assert out[0]["score"] == 1.0


def test_preferred_domains_tolerate_missing_source():
    """source が空でも落ちない（バックエンドによっては欠けうる）。"""
    out = _web_tool(["go.jp"])._prefer_domains([{"score": 0.5, "payload": {}}])

    assert out[0]["preferred_domain"] is False


def test_profile_supplies_preferred_domains_to_config(monkeypatch):
    """run_support_agent_core が業界プロファイルの優先ドメインを配線する。"""
    from backend.app.core import support_agent as core
    from backend.tests.conftest import (
        PipelineStub,
        install_pipeline_stub,
        make_config_stub,
    )

    stub = PipelineStub(config=make_config_stub())
    install_pipeline_stub(monkeypatch, stub)

    captured: dict = {}
    registry_factory = core.create_tool_registry
    monkeypatch.setattr(
        core, "create_tool_registry",
        lambda cfg: (captured.setdefault("cfg", cfg), registry_factory(cfg))[1],
    )

    core.run_support_agent_core("住民票の写しの取り方は？", vertical="gov")

    assert captured["cfg"].web_search.preferred_domains == ["go.jp", "lg.jp"]


def test_generic_run_clears_preferred_domains(monkeypatch):
    """--vertical 未指定は優先ドメイン無し（前リクエストの残留を防ぐ）。"""
    from backend.app.core import support_agent as core
    from backend.tests.conftest import (
        PipelineStub,
        install_pipeline_stub,
        make_config_stub,
    )

    stub = PipelineStub(config=make_config_stub())
    install_pipeline_stub(monkeypatch, stub)

    captured: dict = {}
    registry_factory = core.create_tool_registry
    monkeypatch.setattr(
        core, "create_tool_registry",
        lambda cfg: (captured.setdefault("cfg", cfg), registry_factory(cfg))[1],
    )

    core.run_support_agent_core("パスワードを忘れました", vertical=None)

    assert captured["cfg"].web_search.preferred_domains == []
