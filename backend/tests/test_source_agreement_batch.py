# backend/tests/test_source_agreement_batch.py
"""`source_agreement` の Embedding を**まとめて 1 往復で取る**ことを固定するテスト。

## 背景（実測 2026-08-17 16:17）

`SourceAgreementCalculator.calculate` は `for answer in answers` で 1 件ずつ
`embed_content` を呼んでいた。Web フォールバックで出典が 9 件あると
**1 質問あたり 9 リクエスト**になる。

    16:17:41 → 16:17:45  batchEmbedContents ×9  約 4 秒

#80（RAG ループのクエリベクトル再利用）とは**別の経路**で、あちらを直しても
残っていた。Embedding は外部 API（Gemini）なので、待ち時間だけでなく
**課金にも効く**。`contents` はリストを受けられるので、内容も件数も変えずに
1 往復へ畳める。

## ⚠️ 件数が食い違ったら黙って続けない

cosine 類似度は「返ってきた順番が入力と対応している」前提で取る。ズレたまま
計算すると **別のソース同士を比較した一致度**になり、値は出るのに間違っている
（気付けない）。件数不一致を検知したら 1 件ずつの取得へ落として整合を保つ。

ここで固定すること:
  1. n 件のソースでも API 呼び出しは 1 回
  2. 全ソースが送られること（1 件も落とさない）
  3. 一致度の値が 1 件ずつ取得していた頃と同じであること
  4. 件数不一致は検知して 1 件ずつ取り直すこと（黙って進まない）
  5. 上限（BATCH_SIZE）を超えたら分割すること
  6. 失敗時は従来どおり 0.5（評価を止めない）
  7. 1 件以下なら API を呼ばず 1.0

⚠️ Gemini には接続しない。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from grace.confidence import SourceAgreementCalculator

# 長さがばらけるようにする（全部同じ長さだと一致度が常に 1.0 になり、
# 「値が変わっていない」ことの検証にならない）。
TEXTS = [f"出典 {i} の本文" + "。" * i for i in range(9)]


def _vector(text: str) -> list[float]:
    """本文だけから決まるベクトル。

    ⚠️ **バッチ内の位置に依存させない。** 位置を混ぜると「まとめても値が
    変わらない」ことを厳密に比較できなくなる。
    """
    return [float(len(text)), 1.0, 1.0]


class _Recorder:
    """`embed_content` の呼び出しを記録するスタブ。"""

    def __init__(self, *, returns=None):
        self.calls: list[list[str]] = []
        self._returns = returns

    def embed_content(self, model, contents):
        items = contents if isinstance(contents, list) else [contents]
        self.calls.append(list(items))
        if self._returns is not None:
            return self._returns(items)
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=_vector(t)) for t in items]
        )


def _calc(recorder):
    c = SourceAgreementCalculator.__new__(SourceAgreementCalculator)
    c.config = SimpleNamespace(embedding=SimpleNamespace(model="gemini-embedding-001"))
    c.client = SimpleNamespace(models=recorder)
    c.embed_model = "gemini-embedding-001"
    return c


# =============================================================================
# ① 呼び出しは 1 回
# =============================================================================

class TestBatchedIntoOneRequest:

    def test_nine_sources_take_one_call(self):
        rec = _Recorder()

        _calc(rec).calculate(TEXTS)

        assert len(rec.calls) == 1, (
            f"{len(rec.calls)} 回 API を叩いている（出典数ぶん呼んでいる）"
        )

    def test_every_source_is_sent(self):
        rec = _Recorder()

        _calc(rec).calculate(TEXTS)

        [sent] = rec.calls
        assert sent == TEXTS, "まとめた際にソースを落としている"

    def test_contents_is_a_list(self):
        """`contents` にリストを渡していること（1 件ずつの名残を残さない）。"""
        rec = _Recorder()

        _calc(rec).calculate(TEXTS)

        assert isinstance(rec.calls[0], list) and len(rec.calls[0]) == 9

    def test_agreement_value_is_unchanged(self):
        """1 件ずつ取得していた頃と**同じ値**になること。

        まとめたことで並び順がズレていれば、この期待値と一致しない。
        """
        import itertools

        batched = _calc(_Recorder()).calculate(TEXTS)

        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            return dot / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5))

        vectors = [_vector(t) for t in TEXTS]
        pairs = list(itertools.combinations(vectors, 2))
        expected = sum(cos(a, b) for a, b in pairs) / len(pairs)

        assert expected < 1.0, "期待値が 1.0 だと並び順のズレを検出できない"
        assert batched == pytest.approx(expected)


# =============================================================================
# ② 件数が食い違ったら黙って続けない
# =============================================================================

class TestCountMismatchIsCaught:

    def test_short_response_falls_back_to_one_by_one(self, caplog):
        """3 件送って 2 件しか返らないケース。

        ⚠️ ここで黙って続けると、ソースの対応がズレたまま cosine を取る
        （＝別のソース同士を比較した一致度）。
        """
        state = {"first": True}

        def _returns(items):
            if state["first"] and len(items) > 1:
                state["first"] = False
                return SimpleNamespace(embeddings=[
                    SimpleNamespace(values=[1.0, 0.0, 0.0]) for _ in items[:-1]
                ])
            return SimpleNamespace(embeddings=[
                SimpleNamespace(values=[1.0, 1.0, 1.0]) for _ in items
            ])

        rec = _Recorder(returns=_returns)
        with caplog.at_level(logging.WARNING):
            result = _calc(rec).calculate(["a", "bb", "ccc"])

        assert "1 件ずつ取得し直します" in caplog.text
        # バッチ 1 回 + 1 件ずつ 3 回
        assert len(rec.calls) == 4
        assert [c for c in rec.calls[1:]] == [["a"], ["bb"], ["ccc"]]
        assert result == pytest.approx(1.0)

    def test_empty_embeddings_falls_back(self, caplog):
        def _returns(items):
            if len(items) > 1:
                return SimpleNamespace(embeddings=None)
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 2.0])])

        rec = _Recorder(returns=_returns)
        with caplog.at_level(logging.WARNING):
            result = _calc(rec).calculate(["a", "b"])

        assert "1 件ずつ取得し直します" in caplog.text
        assert result == pytest.approx(1.0)


# =============================================================================
# ③ 上限で分割する
# =============================================================================

class TestChunking:

    def test_over_the_limit_is_split(self, monkeypatch):
        monkeypatch.setattr(SourceAgreementCalculator, "BATCH_SIZE", 4)
        rec = _Recorder()

        _calc(rec).calculate(TEXTS)   # 9 件 / 上限 4 → 3 回

        assert [len(c) for c in rec.calls] == [4, 4, 1]
        assert [t for call in rec.calls for t in call] == TEXTS

    def test_exactly_the_limit_is_one_call(self, monkeypatch):
        monkeypatch.setattr(SourceAgreementCalculator, "BATCH_SIZE", 9)
        rec = _Recorder()

        _calc(rec).calculate(TEXTS)

        assert len(rec.calls) == 1


# =============================================================================
# ④ 従来の契約は変えない
# =============================================================================

class TestContractUnchanged:

    def test_single_source_skips_the_api(self):
        rec = _Recorder()

        assert _calc(rec).calculate(["only one"]) == 1.0
        assert rec.calls == [], "1 件なのに API を呼んでいる"

    def test_empty_skips_the_api(self):
        rec = _Recorder()

        assert _calc(rec).calculate([]) == 1.0
        assert rec.calls == []

    def test_failure_returns_the_neutral_value(self, caplog):
        def _boom(_items):
            raise RuntimeError("Gemini API unavailable")

        rec = _Recorder(returns=_boom)
        with caplog.at_level(logging.ERROR):
            result = _calc(rec).calculate(TEXTS)

        assert result == 0.5, "失敗時は 0.5（評価を止めない）"
        assert "Source agreement calculation error" in caplog.text
