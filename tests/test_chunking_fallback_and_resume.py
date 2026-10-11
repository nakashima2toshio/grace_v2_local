# tests/test_chunking_fallback_and_resume.py
"""チャンク化が本文を黙って落とさないこと・別の入力で再開しないこと・失敗を終了コードで返すこと。

## なぜこのテストがあるか（2026-10-11 に実測した 3 件。`chunking/docs/csv_text_to_chunks_text_csv.md` §5.3）

1. **LLM が 1 ブロックだけ失敗すると、その本文が出力から消えていた。**
   `AsyncAPIClient` はリトライし尽くすと `None` を返すが、Step1・Step2 は `None` を
   飛ばすだけで代わりの処理が無かった（終了コード 0）。失敗したブロック・段落は
   **分割せずに原文のまま残す**。
2. **`--resume` に別の入力を渡しても気づかなかった。** 前のジョブのチャンクが新しい
   入力名の CSV に書かれた。チェックポイントに入力（ファイル名・本文のハッシュ）・
   `block_size`・モデルを残し、食い違えば `ResumeMismatchError` で止める。
3. **入力ファイルが無くても CLI の終了コードが 0 だった。** 1 を返す。

**Ollama は不要**（`AsyncAPIClient` をスタブへ差し替え、CLI の事前確認〔疎通・pull 済みか〕も素通りさせる）。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

import pytest

import chunking.csv_text_to_chunks_text_csv as mod
from chunking.checkpoint_manager import CheckpointManager, ResumeMismatchError
from chunking.models import (
    ContinuityResult,
    ParagraphUnit,
    SentenceUnit,
    StructuralResult,
)

_INPUT_MARK = "【入力テキスト】\n"


class _FakeClient:
    """`AsyncAPIClient` の代わり。`fail` に入れた task_id だけ失敗（None か壊れた JSON）にする。

    成功するときは入力をそのまま 1 段落で返し、Step3 は常に「つながっていない」と答える。
    """

    calls: list = []

    def __init__(self, *, fail=None, broken=None, **_kw):
        self.fail = set(fail or ())
        self.broken = set(broken or ())

    async def generate_content(self, model, contents, response_schema, task_id=None):
        type(self).calls.append(task_id)
        if task_id in self.fail:
            return None
        if task_id in self.broken:
            return "{not json"
        if response_schema is ContinuityResult:
            return ContinuityResult(is_connected=False).model_dump_json()
        body = contents.split(_INPUT_MARK, 1)[1]
        para = ParagraphUnit(id=0, sentences=[SentenceUnit(text=body)])
        return StructuralResult(paragraphs=[para]).model_dump_json()


def _use_fake(monkeypatch, **kw):
    _FakeClient.calls = []
    monkeypatch.setattr(mod, "AsyncAPIClient", lambda **ck: _FakeClient(**kw))


def _flat(s: str) -> str:
    return re.sub(r"\s+", "", s)


TEXT = "".join(f"第{i}文はここにあります。" for i in range(1, 13))  # 132 文字


def _run(tmp_path, text=TEXT, **kw):
    cm = CheckpointManager(checkpoint_dir=str(tmp_path / "ckpt"), job_id="job")
    return asyncio.run(mod.chunks_all_async(
        text, block_size=40, checkpoint_manager=cm, source_file="a.txt", **kw
    ))


# =============================================================================
# 1. 失敗したブロック・段落の本文を残す
# =============================================================================

def test_前提_全部成功すれば本文はそのまま残る(monkeypatch, tmp_path):
    _use_fake(monkeypatch)
    chunks = _run(tmp_path)
    assert _flat("".join(chunks)) == _flat(TEXT)


@pytest.mark.parametrize("kind", ["fail", "broken"])
def test_Step1で失敗したブロックの本文が残る(monkeypatch, tmp_path, kind):
    _use_fake(monkeypatch, **{kind: {"step1_block_1"}})
    chunks = _run(tmp_path)
    # 2 番目のブロック（40〜79 文字目付近）の本文も出力に残る
    assert _flat("".join(chunks)) == _flat(TEXT)


@pytest.mark.parametrize("kind", ["fail", "broken"])
def test_Step2で失敗した段落の本文が残る(monkeypatch, tmp_path, kind):
    _use_fake(monkeypatch, **{kind: {"step2_para_0"}})
    chunks = _run(tmp_path)
    assert _flat("".join(chunks)) == _flat(TEXT)


def test_原文のまま残した件数をチェックポイントに記録する(monkeypatch, tmp_path):
    _use_fake(monkeypatch, fail={"step1_block_2"})
    _run(tmp_path)
    meta = CheckpointManager(checkpoint_dir=str(tmp_path / "ckpt"), job_id="job") \
        .load_with_metadata("step1")["metadata"]
    assert meta["fallback_count"] == 1


# =============================================================================
# 2. 別の入力・設定で再開しない
# =============================================================================

def test_新規ジョブは入力と設定を記録する(monkeypatch, tmp_path):
    _use_fake(monkeypatch)
    _run(tmp_path, model="m1")
    meta = json.loads((tmp_path / "ckpt" / "job" / "job.json").read_text(encoding="utf-8"))
    assert meta["source_file"] == "a.txt"
    assert meta["block_size"] == 40
    assert meta["model"] == "m1"
    assert meta["text_chars"] == len(TEXT)
    assert len(meta["text_sha256"]) == 64


def test_同じ入力と設定なら再開できる(monkeypatch, tmp_path):
    _use_fake(monkeypatch)
    first = _run(tmp_path, model="m1")
    _FakeClient.calls = []
    again = _run(tmp_path, model="m1")
    assert again == first
    assert _FakeClient.calls == []  # Step1〜3 ともチェックポイントから読む


@pytest.mark.parametrize("change", [
    {"text": TEXT + "追加の文。"},
    {"model": "m2"},
    {"block_size": 80},
    {"source_file": "b.txt"},
])
def test_入力か設定が違えば再開しない(monkeypatch, tmp_path, change):
    _use_fake(monkeypatch)
    _run(tmp_path, model="m1")
    _FakeClient.calls = []

    args = {"text": TEXT, "model": "m1", "block_size": 40, "source_file": "a.txt", **change}
    cm = CheckpointManager(checkpoint_dir=str(tmp_path / "ckpt"), job_id="job")
    with pytest.raises(ResumeMismatchError) as excinfo:
        asyncio.run(mod.chunks_all_async(args.pop("text"), checkpoint_manager=cm, **args))
    assert "job" in str(excinfo.value)
    assert _FakeClient.calls == []  # LLM を呼ぶ前に止まる


def test_記録の無い古いチェックポイントは照合せずに再開する(monkeypatch, tmp_path):
    """job.json を書く前に作られたチェックポイントは、照合できないので警告して続ける。"""
    _use_fake(monkeypatch)
    _run(tmp_path, model="m1")
    (tmp_path / "ckpt" / "job" / "job.json").unlink()
    chunks = _run(tmp_path, model="m1")
    assert _flat("".join(chunks)) == _flat(TEXT)
    assert (tmp_path / "ckpt" / "job" / "job.json").exists()


# =============================================================================
# 3. CLI の終了コード
# =============================================================================

def _main(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    # ルートロガーのハンドラを差し替えるので、pytest のログ捕捉を壊さないよう止める
    monkeypatch.setattr(mod, "setup_logging", lambda **_kw: None)
    # Ollama の事前確認（疎通・pull 済みか）は本テストの対象外
    monkeypatch.setattr(mod, "_ollama_unreachable_message", lambda: None)
    monkeypatch.setattr(mod, "_model_not_pulled_message", lambda _m: None)
    monkeypatch.setattr(sys, "argv", ["csv_text_to_chunks_text_csv", *argv])
    return asyncio.run(mod.main())


def test_CLI_入力ファイルが無ければ1を返す(monkeypatch, tmp_path):
    assert _main(monkeypatch, tmp_path, "--input-file", "nope.txt") == 1


def test_CLI_成功すれば0を返す(monkeypatch, tmp_path):
    _use_fake(monkeypatch)
    (tmp_path / "a.txt").write_text(TEXT, encoding="utf-8")
    assert _main(monkeypatch, tmp_path, "--input-file", "a.txt", "--block-size", "40") == 0
    assert (tmp_path / "chunks_output" / "a_chunks.csv").exists()


def test_CLI_別の入力で再開すると1を返し出力を書かない(monkeypatch, tmp_path):
    _use_fake(monkeypatch)
    (tmp_path / "a.txt").write_text(TEXT, encoding="utf-8")
    (tmp_path / "b.txt").write_text("別の文書です。" * 5, encoding="utf-8")
    assert _main(monkeypatch, tmp_path, "--input-file", "a.txt", "--resume", "j1") == 0

    code = _main(monkeypatch, tmp_path, "--input-file", "b.txt", "--resume", "j1")
    assert code == 1
    assert not (tmp_path / "chunks_output" / "b_chunks.csv").exists()


# =============================================================================
# 4. 既定のジョブ ID が重ならないこと
# =============================================================================

def test_同じ秒に作った新規ジョブは別のディレクトリになる(monkeypatch, tmp_path):
    """既定のジョブ ID は秒までの時刻なので、同じ秒の 2 回目は前のジョブを共有していた。

    共有すると、2 回目の実行が 1 回目の途中結果（別の入力のチャンク）を読み戻す。
    照合を入れたあとは、`--resume` を付けていない新規実行が `ResumeMismatchError` で止まる。
    """
    import chunking.checkpoint_manager as ckm

    class _FrozenDatetime:
        @staticmethod
        def now():
            from datetime import datetime
            return datetime(2026, 10, 11, 12, 0, 0)

    monkeypatch.setattr(ckm, "datetime", _FrozenDatetime)
    first = CheckpointManager(checkpoint_dir=str(tmp_path))
    second = CheckpointManager(checkpoint_dir=str(tmp_path))

    assert first.job_id == "20261011_120000"
    assert second.job_dir != first.job_dir
    # 新しいジョブほど名前の降順で前に来る（list_jobs / cleanup_old_jobs の並び）
    assert sorted([first.job_id, second.job_id], reverse=True)[0] == second.job_id


def test_ジョブIDを指定すれば同じディレクトリを使う(tmp_path):
    first = CheckpointManager(checkpoint_dir=str(tmp_path), job_id="j")
    second = CheckpointManager(checkpoint_dir=str(tmp_path), job_id="j")
    assert first.job_dir == second.job_dir


def test_所要時間の表の直後に原文のまま残した件数を出す(monkeypatch, tmp_path, caplog):
    """grace_v2_local だけにある所要時間の表（`_log_timing_summary`）の直後に 1 行で出す。"""
    import logging

    _use_fake(monkeypatch, fail={"step1_block_1", "step2_para_0"})
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        _run(tmp_path)
    lines = [r.getMessage() for r in caplog.records if "原文のまま残した:" in r.getMessage()]
    assert lines == ["  ⚠️ LLM が失敗して原文のまま残した: step1 1 ブロック / step2 1 段落"]


def test_全部成功すれば原文のまま残した件数は出さない(monkeypatch, tmp_path, caplog):
    import logging

    _use_fake(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        _run(tmp_path)
    assert not [r for r in caplog.records if "原文のまま残した" in r.getMessage()]
