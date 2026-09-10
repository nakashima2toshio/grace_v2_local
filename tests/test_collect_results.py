"""#67: collect_results の完了順回収（HOLブロッキング解消）を検証する。

実 Redis/Celery 不要。AsyncResult を模した FakeTask で ready()/get() を制御し、
- 遅いタスクが速い後続タスクの回収を塞がない（完了順）
- on_result が完了順に呼ばれる
- usage_out にワーカー使用量が集約される
- 新形式(dict) / 旧形式(list) の両方を吸収する
- タイムアウトが失敗としてカウントされる
を確認する。
"""
from celery_tasks import collect_results


class FakeTask:
    """AsyncResult 互換の最小モック。ready() を n 回呼ぶと True になる。"""

    def __init__(self, result, ready_after=1):
        self._result = result
        self._ready_after = ready_after
        self._calls = 0

    def ready(self):
        self._calls += 1
        return self._calls >= self._ready_after

    def get(self, timeout=None):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def test_completion_order_not_submission_order():
    """先頭の遅いタスクが、後続の速いタスクの回収を塞がないこと。"""
    order = []
    t_slow = FakeTask({"qa_pairs": [{"q": "s"}], "usage": {}}, ready_after=5)
    t_fast = FakeTask({"qa_pairs": [{"q": "f"}], "usage": {}}, ready_after=1)

    collect_results([t_slow, t_fast], timeout=10,
                    on_result=lambda idx, qp: order.append(idx))

    # 投入順は [slow, fast] だが、完了順は fast(idx=1) が先
    assert order[0] == 1
    assert set(order) == {0, 1}


def test_usage_aggregation_and_legacy_list():
    """usage_out 集約と、新形式(dict)/旧形式(list)の両吸収。"""
    t_dict = FakeTask({"qa_pairs": [{"q": "a"}],
                       "usage": {"input_tokens": 3, "output_tokens": 4}})
    t_list = FakeTask([{"q": "legacy"}])  # 旧形式
    usage = {}

    res = collect_results([t_dict, t_list], timeout=10, usage_out=usage)

    assert len(res) == 2
    assert usage == {"input_tokens": 3, "output_tokens": 4}


def test_qa_count_zero_is_success_and_notified():
    """qa_count=0（空リスト）も成功・on_result 通知される（処理済み扱い）。"""
    notified = []
    t_empty = FakeTask({"qa_pairs": [], "usage": {}})

    res = collect_results([t_empty], timeout=10,
                          on_result=lambda idx, qp: notified.append((idx, qp)))

    assert res == []
    assert notified == [(0, [])]


def test_timeout_counts_as_failure():
    """期限内に ready() にならないタスクはタイムアウト失敗となる。"""
    t_never = FakeTask({"qa_pairs": [{"q": "x"}], "usage": {}}, ready_after=10_000)

    res = collect_results([t_never], timeout=1)

    assert res == []
