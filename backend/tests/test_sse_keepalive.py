# backend/tests/test_sse_keepalive.py
"""SSE の keepalive が **JS から見える名前付きイベント**であることを固定するテスト。

## 背景（2026-10-08 の実例）

ローカル LLM のチャンク化（約 70 分）で、Step 2 が 38 分ログを出さない間に
画面への配信が黙って止まった。処理は最後まで終わって CSV も書けていたのに、
画面は途中のまま固まり、エラー表示も出なかった。

フロント（`frontend/src/api/client.ts::subscribeStream`）は、何も届かない時間が
続いたら張り直すように直した。その「生きている」判断の材料が keepalive である。
以前の keepalive はコメント行（`: keepalive`）で、**EventSource はコメントを捨てる**ため
JS から見えなかった。コメントに戻すと、静かなだけの接続を 1 分ごとに張り直すようになる。

ここで固定すること:
  1. keepalive は `event: keepalive` の名前付きイベント（コメント行ではない）
  2. `data:` 行を持つ（SSE の仕様上、data が空のイベントは配信されない）
  3. Support / Review / データ準備の 3 つのストリームがすべて同じ keepalive を送る
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.core.jobs import SSE_KEEPALIVE, job_manager
from backend.app.main import app

client = TestClient(app)


def test_keepalive_is_a_named_event_not_a_comment():
    lines = SSE_KEEPALIVE.split("\n")
    assert not SSE_KEEPALIVE.startswith(":"), "コメント行は EventSource が捨てるので JS から見えない"
    assert lines[0] == "event: keepalive"
    assert any(line.startswith("data:") for line in lines), "data が無いイベントは配信されない"
    assert SSE_KEEPALIVE.endswith("\n\n"), "空行で終わらないとイベントが確定しない"


class _QuietJob:
    """新イベントが来ない（keepalive を 1 回出して終わる）ジョブの代役。"""

    status = "completed"
    finished_at = 2.0
    created_at = 1.0

    def stream_events(self, poll_timeout: float = 15.0):
        yield None


@pytest.mark.parametrize("kind", ["support", "review", "data"])
def test_every_stream_sends_the_named_keepalive(monkeypatch, kind):
    monkeypatch.setattr(job_manager, "get", lambda job_id: _QuietJob())
    body = client.get(f"/api/{kind}/stream/quiet").text
    assert SSE_KEEPALIVE in body
    assert not any(line.startswith(":") for line in body.split("\n")), "コメント行の keepalive が残っている"
    # keepalive の後に終端の done が来る（既存の契約）
    assert body.index(SSE_KEEPALIVE) < body.index('"type": "done"')


def test_keepalive_constant_is_shared():
    """3 つのエンドポイントが同じ定数を使っている（1 か所だけ直す取り残しを防ぐ）。"""
    import backend.app.api.data as data_api
    import backend.app.api.review as review_api
    import backend.app.api.support as support_api

    for module in (data_api, review_api, support_api):
        assert getattr(module, "SSE_KEEPALIVE", None) is SSE_KEEPALIVE, module.__name__
