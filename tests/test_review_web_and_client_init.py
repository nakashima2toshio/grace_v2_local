# tests/test_review_web_and_client_init.py
"""実測 2026-09-30 で見つかった 2 件の回帰を固定するテスト。

## A. ⑥ Web 裏取りが直列で、遅い 1 回が全体の半分を占めた

2 回の検索を直列で待ち、2 回目の SerpAPI が 23 秒かかって Review 全体 44 秒の
半分を占めた。裏取りは判定を変えない補助なので、**並列**に走らせ、全体で
`GRACE_REVIEW_WEB_TIMEOUT` 秒（既定 5）しか待たない。
あわせて、`web_checked` は**検索が結果を返したルールにだけ**付ける
（以前は失敗・タイムアウトでも付き、「裏取り済み」と出るのに確認できていなかった）。

## B. 遅延生成するクライアントが並列で重複作成された

Retrieve の並列化で、最初のリクエストに 4 スレッドが同時に「まだ無い」と判断し、
Embedding / Sparse クライアントをそれぞれ作った（SPLADE を 4 重に読み込み）。

⚠️ LLM にも Qdrant にもネットワークにも接続しない。
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

import qdrant_client_wrapper as qcw
from backend.app.core.review_agent import _web_crosscheck
from backend.app.core.rulesets import get_ruleset

EC_AD = get_ruleset("ec_ad")
WEB_RULES = [r.rule_id for r in EC_AD.rules if r.web_check]


def _findings(rule_ids):
    return [SimpleNamespace(rule_id=rid, web_checked=False) for rid in rule_ids]


def _ok(_name, **_kw):
    return SimpleNamespace(success=True, output=[{"score": 1.0}])


class TestWebCrosscheck:

    def test_needs_at_least_two_web_rules(self):
        assert len(WEB_RULES) >= 2, "このテストは web_check ルールが 2 つ以上あることを前提にする"

    def test_searches_run_in_parallel(self):
        """2 件の検索が同時に走ること（直列なら Barrier が解けず失敗）。"""
        barrier = threading.Barrier(2, timeout=5)

        def execute(name, **kw):
            barrier.wait()
            return _ok(name)

        registry = SimpleNamespace(execute=execute)
        findings = _findings(WEB_RULES[:2])
        assert _web_crosscheck(registry, findings, EC_AD, lambda *a, **k: None) is True
        assert all(f.web_checked for f in findings)

    def test_slow_search_does_not_block_and_is_not_marked(self, monkeypatch):
        monkeypatch.setenv("GRACE_REVIEW_WEB_TIMEOUT", "0.3")
        slow_rule = EC_AD.rule_by_id(WEB_RULES[0])
        release = threading.Event()

        def execute(name, query, **kw):
            if slow_rule.article in query and slow_rule.law in query:
                release.wait(5)          # 遅い検索（テスト末尾で解放する）
            return _ok(name)

        registry = SimpleNamespace(execute=execute)
        findings = _findings(WEB_RULES[:2])
        logs = []
        started = time.monotonic()
        try:
            _web_crosscheck(registry, findings, EC_AD, lambda m, **k: logs.append(m))
            elapsed = time.monotonic() - started
        finally:
            release.set()

        assert elapsed < 2.0, f"遅い検索を待ってしまった: {elapsed:.1f}s"
        assert any("見送り" in m for m in logs)
        # 遅かったルールは付かず、速かったルールだけ付く
        checked = {f.rule_id: f.web_checked for f in findings}
        assert checked[WEB_RULES[0]] is False

    def test_failed_search_is_not_marked_checked(self):
        def execute(name, **kw):
            raise RuntimeError("network down")

        findings = _findings(WEB_RULES[:1])
        used = _web_crosscheck(
            SimpleNamespace(execute=execute), findings, EC_AD, lambda *a, **k: None
        )
        assert used is False
        assert findings[0].web_checked is False

    def test_no_web_rules_returns_quickly(self):
        non_web = [r.rule_id for r in EC_AD.rules if not r.web_check][:1]
        findings = _findings(non_web)
        assert _web_crosscheck(
            SimpleNamespace(execute=_ok), findings, EC_AD, lambda *a, **k: None
        ) is False
        assert findings[0].web_checked is False

    @pytest.mark.parametrize("raw", ["abc", "0", "-1", ""])
    def test_invalid_timeout_falls_back_to_default(self, monkeypatch, raw):
        from backend.app.core.review_agent import DEFAULT_WEB_TIMEOUT, _web_timeout

        monkeypatch.setenv("GRACE_REVIEW_WEB_TIMEOUT", raw)
        assert _web_timeout() == DEFAULT_WEB_TIMEOUT

    def test_default_timeout_is_five_seconds(self, monkeypatch):
        """既定は 5 秒（実測: 返る検索は 1.7 秒以内、遅い検索は 14.8 秒以上で中間が無い）。

        10 秒のときは、遅い検索のたびに Review 全体が 10 秒待たされた
        （2026-10-01: 33 秒中 10 秒）。理由は `DEFAULT_WEB_TIMEOUT` の宣言箇所。
        """
        from backend.app.core.review_agent import _web_timeout

        monkeypatch.delenv("GRACE_REVIEW_WEB_TIMEOUT", raising=False)
        assert _web_timeout() == 5.0


class TestLazyClientsAreCreatedOnce:

    @staticmethod
    def _hammer(fn, threads=6):
        barrier = threading.Barrier(threads, timeout=5)
        results = []

        def run():
            barrier.wait()
            results.append(fn())

        ts = [threading.Thread(target=run) for _ in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return results

    def test_embedding_client_is_created_once(self, monkeypatch):
        created = []

        def slow_create(provider=None, **kw):
            created.append(provider)
            time.sleep(0.1)          # 他スレッドが「まだ無い」と判断できる隙間
            return object()

        monkeypatch.setattr(qcw, "_embedding_clients", {})
        monkeypatch.setattr(qcw, "create_embedding_client", slow_create)

        results = self._hammer(lambda: qcw.get_embedding_client("gemini"))

        assert len(created) == 1
        assert len({id(r) for r in results}) == 1

    def test_sparse_client_is_created_once(self, monkeypatch):
        created = []

        def slow_create(model_name=None):
            created.append(model_name)
            time.sleep(0.1)
            return object()

        monkeypatch.setattr(qcw, "_sparse_embedding_clients", {})
        monkeypatch.setattr(qcw, "get_sparse_embedding_client", slow_create)

        results = self._hammer(lambda: qcw.get_cached_sparse_embedding_client(None))

        assert len(created) == 1
        assert len({id(r) for r in results}) == 1
