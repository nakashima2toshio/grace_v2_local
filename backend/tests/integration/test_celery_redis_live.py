"""Celery（Q/A 生成の並列実行）を実 Redis 経由で往復させる結合テスト。

テスト内で本物の Celery ワーカー（solo プール・同一プロセスのスレッド）を立て、
``submit_unified_qa_generation`` → Redis（broker）→ ``generate_qa_for_chunk_task``
→ Redis（result backend）→ ``collect_results`` を通す。
LLM（``SmartQAGenerator``）だけを固定応答の生成器に差し替えるので API キーは不要。

スタブでは見えない次の点を確かめる:
JSON シリアライズを経た戻り値の形・usage の集約・空チャンク・完了順コールバック。

⚠️ Redis は db 15 を使う（conftest.py）。Celery アプリは接続をキャッシュするので、
   切り替え時にキャッシュを捨て、**db 15 を向いたことを確認してから**投入する。
   Mac で常駐している本物のワーカー（db 0）へテストのタスクを流さないため。
"""

import pytest

pytestmark = pytest.mark.integration


def _reset_connections(app) -> None:
    # Celery 5.x は broker 接続プール・result backend・producer プールを遅延生成して保持する。
    # backend はスレッドセーフなら _backend_cache、そうでなければスレッドローカルに入る。
    app._pool = None
    app._backend_cache = None
    app._local.__dict__.pop("backend", None)
    app.amqp._producer_pool = None


@pytest.fixture(scope="module")
def celery_app_on_test_db(redis_url):
    from celery_config import app

    original = {
        "broker_url": app.conf.broker_url,
        "result_backend": app.conf.result_backend,
        "worker_disable_rate_limits": app.conf.worker_disable_rate_limits,
    }
    _reset_connections(app)
    # 本番の rate_limit（generate_qa_for_chunk は 60/m）が効くと 3 タスクで約 10 秒かかる
    # （実測）。ここで見たいのは Redis 往復なので、テスト中だけ外す。
    app.conf.update(broker_url=redis_url, result_backend=redis_url, worker_disable_rate_limits=True)

    # 取り違えたまま投入すると本物のキューへ流れるので、ここで止める
    assert app.connection_for_write().as_uri().endswith(redis_url.rsplit("/", 1)[-1])
    assert app.backend.url == redis_url

    yield app

    _reset_connections(app)
    app.conf.update(**original)


@pytest.fixture(scope="module")
def celery_worker(celery_app_on_test_db):
    from celery.contrib.testing.worker import start_worker

    with start_worker(
        celery_app_on_test_db, pool="solo", perform_ping_check=False,
        loglevel="WARNING", shutdown_timeout=10,
    ) as worker:
        yield worker


class FakeGenerator:
    """SmartQAGenerator.process_chunk と同じ形の戻り値を返す。"""

    def process_chunk(self, text):
        return {
            "success": True,
            "qa_pairs": [
                {"question": f"Q:{text}", "answer": f"A:{text}", "topic": "t"},
                {"question": f"Q2:{text}", "answer": f"A2:{text}"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 4},
        }


@pytest.fixture
def fake_generator(monkeypatch):
    import celery_tasks

    models_seen = []

    def _get_generator(model):
        models_seen.append(model)
        return FakeGenerator()

    # solo ワーカーは同一プロセスのスレッドで動くので、モジュール属性の差し替えが効く
    monkeypatch.setattr(celery_tasks, "_get_generator", _get_generator)
    return models_seen


def test_qa_generation_roundtrip_through_redis(celery_worker, fake_generator):
    import celery_tasks

    chunks = [
        {"id": "c1", "text": "返品について"},
        {"id": "c2", "text": "   "},  # 空チャンク → Q/A 0 件で正常終了
        {"id": "c3", "text": "送料について"},
    ]
    notified = []
    usage = {}

    tasks = celery_tasks.submit_unified_qa_generation(chunks, {"type": "ec"}, "test-model")
    qa_pairs = celery_tasks.collect_results(
        tasks, timeout=60,
        on_result=lambda idx, pairs: notified.append((idx, len(pairs))),
        usage_out=usage,
    )

    assert len(qa_pairs) == 4
    assert {p["chunk_id"] for p in qa_pairs} == {"c1", "c3"}
    assert {p["dataset_type"] for p in qa_pairs} == {"ec"}
    assert all(set(p) == {"question", "answer", "chunk_id", "topic", "dataset_type"} for p in qa_pairs)
    # topic を返さなかったペアは空文字で埋まる
    assert sorted(p["topic"] for p in qa_pairs) == ["", "", "t", "t"]
    # 空チャンクも「処理済み」として通知される
    assert sorted(notified) == [(0, 2), (1, 0), (2, 2)]
    # usage は空チャンク（0/0）を含めてワーカー側の値を合算する
    assert usage == {"input_tokens": 20, "output_tokens": 8}
    assert fake_generator == ["test-model", "test-model"]


def test_results_are_stored_in_redis_backend(celery_worker, fake_generator, redis_url):
    """結果は db 15 の result backend に JSON で残る（別プロセスからも引ける）。"""
    import redis

    import celery_tasks

    [task] = celery_tasks.submit_unified_qa_generation(
        [{"id": "only", "text": "x"}], {"type": "t"}, "m"
    )
    task.get(timeout=30)

    raw = redis.Redis.from_url(redis_url).get(f"celery-task-meta-{task.id}")
    assert raw is not None
    assert b'"status": "SUCCESS"' in raw
