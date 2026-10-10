"""チャンク化の所要時間を**実測して出す**ことのテスト。

## なぜこのファイルがあるのか

チャンク化が遅い／止まらないという症状を 4 回追いかけたが、そのたびに
「1 リクエストあたり何秒かかっているのか」を tqdm の s/it から目視で
拾っていた。画面を流れて消えるうえ、条件（ワーカー数・モデル）を変えて
比べることができない。

    Step1: 段落分割:  0%| | 1/1229 [09:03<185:17:07, 543.18s/it]

543 秒/ブロックは `CHUNKING_LLM_TIMEOUT` の既定 180 秒 × リトライ 3 回で、
**全リクエストがタイムアウトしていた**ことを意味する。この数字が実行の
最後にまとめて残っていれば、1 回の実行で原因まで届いた。

そこで 3 段階それぞれの所要時間と 1 リクエストあたりの秒数を、
ワーカー数・モデルと一緒に出す。
"""
from __future__ import annotations

import asyncio
import logging

import chunking.csv_text_to_chunks_text_csv as cm


def _run_chunking(monkeypatch, *, max_workers: int = 4) -> None:
    """LLM を呼ばずに `chunks_all_async` を 1 周させる。"""
    class _StubClient:
        def __init__(self, **_kwargs):
            pass

    monkeypatch.setattr(cm, "AsyncAPIClient", _StubClient)

    async def _fake_step(chunks_or_text, _client, _model, *args, **kwargs):
        return ["チャンク1", "チャンク2"]

    monkeypatch.setattr(cm, "_step1_hierarchical_split", _fake_step)
    monkeypatch.setattr(cm, "_step2_semantic_chunking", _fake_step)
    monkeypatch.setattr(cm, "_step3_continuity_check", _fake_step)
    monkeypatch.setattr(cm, "_enforce_max_chunk_tokens", lambda chunks, _limit: chunks)

    asyncio.run(
        cm.chunks_all_async(
            "本文" * 1000, model="gemma4:12b-mlx", max_workers=max_workers
        )
    )


def test_timing_summary_is_emitted(monkeypatch, caplog):
    """**3 段階それぞれの所要時間が実行の最後に残る。**

    回帰: 所要時間は tqdm の s/it にしか出ておらず、実行が終わると
    画面から消えていた。
    """
    with caplog.at_level(logging.INFO, logger=cm.logger.name):
        _run_chunking(monkeypatch)

    text = caplog.text
    assert "所要時間（実測）" in text, "所要時間のサマリが出ていない"
    for label in ("Step1 段落分割", "Step2 意味的分割", "Step3 連続性チェック"):
        assert label in text, f"{label} の行が無い"
    assert "秒/件" in text, "1 リクエストあたりの秒数が出ていない"


def test_summary_records_the_worker_count(monkeypatch, caplog):
    """ワーカー数を一緒に出す。

    1 件あたりの秒数は**待ち時間込み**なので、いくつ同時に投げた結果なのかが
    分からないと比較できない。ローカル LLM は同時実行で 1 件あたりが伸びる
    （`async_api_client.py` の失敗時ヒントも同じことを言っている）。
    """
    with caplog.at_level(logging.INFO, logger=cm.logger.name):
        _run_chunking(monkeypatch, max_workers=3)

    assert "並列ワーカー数: 3" in caplog.text


def test_summary_survives_zero_division(monkeypatch, caplog):
    """件数 0 の段があっても落ちない（サマリのせいで本処理を壊さない）。"""
    cm._log_timing_summary([("Step1 段落分割", 1.5, 0)], max_workers=1, model="m")
    cm._log_timing_summary([], max_workers=1, model="m")


def test_per_call_seconds_is_elapsed_divided_by_calls(caplog):
    """秒/件は「所要秒 ÷ 件数」であること（表示だけの飾りにしない）。"""
    with caplog.at_level(logging.INFO, logger=cm.logger.name):
        cm._log_timing_summary(
            [("Step1 段落分割", 60.0, 20)], max_workers=8, model="gemma4:12b-mlx"
        )

    line = next(
        r.getMessage() for r in caplog.records if "Step1 段落分割" in r.getMessage()
    )
    assert "3.00 秒/件" in line, f"60 秒 / 20 件 = 3.00 秒/件 のはず: {line!r}"
