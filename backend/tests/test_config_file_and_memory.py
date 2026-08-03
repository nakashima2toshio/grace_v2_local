# backend/tests/test_config_file_and_memory.py
"""`config/grace_config.yml` の妥当性と、実行メモリ層の破損耐性の回帰テスト。

## config/grace_config.yml

これまで grace_v2 には設定ファイルが存在せず、`ConfigLoader` は毎回
「ファイルが無いのでクラス既定値を使う」経路で動いていた。設定ファイルを
置いた瞬間に既定値がサイレントに変わると、以後の挙動差の原因追跡が難しい。
そこで **ファイルの内容がクラス既定値と完全一致すること** を固定する
（意図的に既定から外したいときは、このテストを更新して差分を明示する）。

## ExecutionMemory.load

`logs/grace_memory.jsonl` に git のコンフリクトマーカーが混入し、
`ExecutionMemory.load failed: Expecting value: line 1 column 1` で
**全レコードが失われて** いた。JSONL は追記専用なので、書き込み中断や
手編集でも壊れた行は混ざりうる。1 行の破損で全件を捨てないことを固定する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "grace_config.yml"

# ---------------------------------------------------------------------------
# config/grace_config.yml
# ---------------------------------------------------------------------------


def _flatten(d: dict, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def test_config_file_exists_at_loader_default_path():
    """ConfigLoader の既定パスと実ファイルの位置が一致している。"""
    from grace.config import ConfigLoader

    assert CONFIG_PATH.exists()
    assert (REPO_ROOT / ConfigLoader.DEFAULT_CONFIG_PATH) == CONFIG_PATH


def test_config_file_matches_code_defaults():
    """設定ファイルを読んでもクラス既定値と一致する（挙動を変えない）。"""
    from grace.config import GraceConfig

    from_file = GraceConfig(**yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))

    assert _flatten(from_file.model_dump()) == _flatten(GraceConfig().model_dump())


def test_config_file_has_no_unknown_keys():
    """未知キーはタイプミスの温床（pydantic は既定で黙って無視する）。"""
    from grace.config import GraceConfig

    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    fields = GraceConfig.model_fields

    unknown_sections = set(raw) - set(fields)
    assert not unknown_sections, f"未知のセクション: {unknown_sections}"

    for section, values in raw.items():
        if not isinstance(values, dict):
            continue
        annotation = fields[section].annotation
        sub_fields = getattr(annotation, "model_fields", None)
        if sub_fields is None:
            continue
        unknown = set(values) - set(sub_fields)
        assert not unknown, f"{section} に未知のキー: {unknown}"


def test_config_file_holds_no_api_keys():
    """APIキーは .env / 環境変数から。設定ファイルへ書かない。"""
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    for key, value in _flatten(raw).items():
        if "api_key" in key or "engine_id" in key:
            pytest.fail(f"{key} が設定ファイルに書かれている: {value!r}")


def test_config_file_leaves_request_scoped_fields_empty():
    """リクエストごとに注入されるフィールドは固定しない。

    `qdrant.allowed_collections` / `llm.prompt_addendum` は
    `run_support_agent_core` が業界プロファイルから注入する。ここで値を
    固定すると、プロファイル選択が効かない・全業種が同じスコープになる。
    """
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    assert raw["qdrant"].get("allowed_collections", []) == []
    assert "prompt_addendum" not in raw["llm"]


# ---------------------------------------------------------------------------
# ExecutionMemory.load の破損耐性
# ---------------------------------------------------------------------------


def _memory(path: Path):
    from grace.memory import ExecutionMemory

    return ExecutionMemory(path=str(path))


def _record(collection: str, confidence: float = 0.8) -> str:
    return json.dumps(
        {
            "query": "住民票の写しの取り方は？",
            "keywords": ["住民票"],
            "collection": collection,
            "success": True,
            "confidence": confidence,
            "timestamp": 1785000000.0,
        },
        ensure_ascii=False,
    )


def test_load_skips_conflict_markers(tmp_path):
    """コンフリクトマーカーが混ざっても有効行はすべて読める。

    実際に踏んだ形（`<<<<<<<` / `=======` / `>>>>>>>` の 3 行が混入）。
    旧実装は try が for 全体を囲んでいたため、5 行目のマーカーで打ち切られ
    **0 件** になっていた。
    """
    path = tmp_path / "grace_memory.jsonl"
    path.write_text(
        "\n".join([
            _record("a"),
            "<<<<<<< Updated upstream",
            _record("b"),
            "=======",
            _record("c"),
            ">>>>>>> Stashed changes",
            _record("d"),
        ]) + "\n",
        encoding="utf-8",
    )

    records = _memory(path).load()

    assert [r.collection for r in records] == ["a", "b", "c", "d"]


def test_load_skips_truncated_tail(tmp_path):
    """書き込み中断で末尾が切れていても、それ以前は失わない。"""
    path = tmp_path / "grace_memory.jsonl"
    path.write_text(_record("a") + "\n" + '{"query": "途中で', encoding="utf-8")

    assert [r.collection for r in _memory(path).load()] == ["a"]


def test_load_returns_empty_when_missing(tmp_path):
    """ファイルが無ければ空（既存挙動の不変性）。"""
    assert _memory(tmp_path / "nope.jsonl").load() == []


def test_load_warns_once_per_load(tmp_path, caplog):
    """破損行はまとめて 1 回警告する（1 行ごとにログを溢れさせない）。"""
    path = tmp_path / "grace_memory.jsonl"
    path.write_text("\n".join(["broken", "also broken", _record("a")]) + "\n", encoding="utf-8")

    with caplog.at_level("WARNING", logger="grace.memory"):
        records = _memory(path).load()

    assert len(records) == 1
    warnings = [r for r in caplog.records if "skipped" in r.getMessage()]
    assert len(warnings) == 1
    assert "skipped 2 malformed line(s)" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# リポジトリ同梱の logs/grace_memory.jsonl
# ---------------------------------------------------------------------------


def test_repo_memory_file_is_valid_jsonl():
    """同梱ファイルに壊れた行を残さない（コンフリクトマーカーの再混入検知）。"""
    path = REPO_ROOT / "logs" / "grace_memory.jsonl"
    if not path.exists():
        pytest.skip("logs/ は gitignore 対象。存在しない環境ではスキップ")

    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as e:
            pytest.fail(f"logs/grace_memory.jsonl:{i} が不正: {e}")
