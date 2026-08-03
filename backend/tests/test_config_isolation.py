# backend/tests/test_config_isolation.py
"""リクエスト単位の設定分離の回帰テスト（P-08 並行実行時の競合）。

背景:
`get_config()` はプロセス共有のシングルトン（`grace/config.py::ConfigLoader`）
を返す。`run_support_agent_core` は業界プロファイルを反映するために

    config.qdrant.allowed_collections = list(profile.collections)
    config.llm.prompt_addendum        = profile.build_prompt_addendum()

とシングルトンを直接書き換えていた。一方 `backend/app/core/jobs.py::JobManager.start`
は 1 ジョブ = 1 ワーカースレッドで `run_support_agent_core` を回すため、
**同時に 2 リクエストが走ると後発が先発の検索スコープを上書きする**。
gov のリクエストが ec のコレクションだけを検索する、といった取り違えが起きる。

修正は `copy.deepcopy(get_config())` によるリクエスト単位のコピー。
以降の生成物（planner / executor / tools / verifier …）はすべてこのコピーを
参照するため、スレッド間で干渉しない。

検証すること:
- 生成物へ渡る config がプロファイルごとに正しい値を持つ
- 並行実行しても互いのスコープを壊さない
- 共有シングルトンそのものが書き換わらない（副作用を残さない）
"""
from __future__ import annotations

import threading

import pytest

from backend.tests.conftest import PipelineStub, install_pipeline_stub, make_config_stub


def _run_and_capture(monkeypatch, vertical: str, captured: dict, barrier=None,
                     shared_config=None):
    """1 リクエスト分を走らせ、tools へ渡った config を記録する。

    `shared_config` を与えると、複数の呼び出しが **同一の config オブジェクト**
    を `get_config()` から受け取る（＝本番のシングルトン共有を再現する）。
    与えない場合は呼び出しごとに独立した設定になる。

    `barrier` を与えると「プロファイル適用後・実行前」で他スレッドと待ち合わせ、
    シングルトン共有時の上書きを確実に再現する。
    """
    from backend.app.core import support_agent as core

    stub = PipelineStub(config=shared_config or make_config_stub())
    install_pipeline_stub(monkeypatch, stub)

    # create_tool_registry は install_pipeline_stub がスタブ化済み。
    # ここで包み直し、「実際に tools へ渡された config」を掴む。
    registry_factory = core.create_tool_registry

    def capturing_factory(cfg):
        captured[vertical] = cfg
        return registry_factory(cfg)

    monkeypatch.setattr(core, "create_tool_registry", capturing_factory)

    # executor.execute の直前で待ち合わせる（プロファイル適用は既に済んでいる）
    if barrier is not None:
        executor_factory = core.create_executor

        def barrier_executor(cfg, registry):
            executor = executor_factory(cfg, registry)
            inner = executor.execute

            def execute(plan):
                barrier.wait(timeout=5)
                return inner(plan)

            executor.execute = execute
            return executor

        monkeypatch.setattr(core, "create_executor", barrier_executor)

    core.run_support_agent_core("住民票の写しの取り方は？", vertical=vertical)


# ---------------------------------------------------------------------------
# 単発実行: プロファイルが正しく配線される（既存挙動の不変性）
# ---------------------------------------------------------------------------

def test_profile_is_wired_into_tools_config(monkeypatch):
    """gov のコレクションと方針が tools へ渡る config に反映される。"""
    from backend.app.core.verticals import PROFILES

    captured: dict = {}
    _run_and_capture(monkeypatch, "gov", captured)

    cfg = captured["gov"]
    assert cfg.qdrant.allowed_collections == list(PROFILES["gov"].collections)
    assert cfg.llm.prompt_addendum == PROFILES["gov"].build_prompt_addendum()


def test_no_vertical_clears_scope(monkeypatch):
    """--vertical 未指定なら制限なし（空リスト）になる。"""
    captured: dict = {}
    _run_and_capture(monkeypatch, None, captured)

    cfg = captured[None]
    assert cfg.qdrant.allowed_collections == []
    assert cfg.llm.prompt_addendum == ""


# ---------------------------------------------------------------------------
# 本題: 並行実行しても互いのスコープを壊さない
# ---------------------------------------------------------------------------

def test_concurrent_verticals_do_not_leak(monkeypatch):
    """gov と ec を同時に走らせても、それぞれ自分のスコープを保つ。

    修正前はシングルトンを共有していたため、バリアで足並みを揃えると
    両者が同じ（＝後勝ちの）allowed_collections を見てしまう。

    **両スレッドが同じ config オブジェクトを `get_config()` から受け取る**
    ようにしているのが要点。ここを別オブジェクトにするとスタブ側で勝手に
    分離されてしまい、修正前のコードでもテストが通ってしまう。
    """
    from backend.app.core.verticals import PROFILES

    captured: dict = {}
    errors: list = []
    # 本番の get_config() シングルトンに相当する共有オブジェクト
    shared_config = make_config_stub()
    # 2 スレッドが「プロファイル適用後・実行前」で必ず揃うようにする
    barrier = threading.Barrier(2, timeout=5)

    def worker(vertical: str):
        # monkeypatch はスレッドセーフではないため、スレッドごとに独自の
        # MonkeyPatch を用意して undo まで自スレッドで完結させる
        mp = pytest.MonkeyPatch()
        try:
            _run_and_capture(mp, vertical, captured, barrier=barrier,
                             shared_config=shared_config)
        except Exception as exc:  # noqa: BLE001 - 呼び出し元へ転送して落とす
            errors.append(exc)
        finally:
            mp.undo()

    threads = [
        threading.Thread(target=worker, args=(v,), name=f"job-{v}")
        for v in ("gov", "ec")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"ワーカーが例外で終了: {errors}"
    assert set(captured) == {"gov", "ec"}

    # 互いのスコープが混ざっていないこと
    assert captured["gov"].qdrant.allowed_collections == list(PROFILES["gov"].collections)
    assert captured["ec"].qdrant.allowed_collections == list(PROFILES["ec"].collections)
    assert captured["gov"].llm.prompt_addendum == PROFILES["gov"].build_prompt_addendum()
    assert captured["ec"].llm.prompt_addendum == PROFILES["ec"].build_prompt_addendum()

    # 別インスタンスであること（同一オブジェクトなら分離できていない）
    assert captured["gov"] is not captured["ec"]


def test_shared_singleton_is_not_mutated(monkeypatch):
    """実行後も共有シングルトンは書き換わらない（副作用を残さない）。"""
    from backend.app.core import support_agent as core

    shared = make_config_stub()
    shared.qdrant.allowed_collections = ["sentinel_collection"]
    shared.llm.prompt_addendum = "sentinel"

    stub = PipelineStub(config=shared)
    install_pipeline_stub(monkeypatch, stub)

    captured: dict = {}
    registry_factory = core.create_tool_registry

    def capturing_factory(cfg):
        captured["cfg"] = cfg
        return registry_factory(cfg)

    monkeypatch.setattr(core, "create_tool_registry", capturing_factory)

    core.run_support_agent_core("住民票の写しの取り方は？", vertical="gov")

    # コピー側にはプロファイルが乗り、共有側は sentinel のまま
    assert captured["cfg"].qdrant.allowed_collections != ["sentinel_collection"]
    assert shared.qdrant.allowed_collections == ["sentinel_collection"]
    assert shared.llm.prompt_addendum == "sentinel"
