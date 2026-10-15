"""
GRACE 実行メモリ層（P4）

実行ログから「(質問キーワード, 当たったコレクション, 成否, confidence)」を
蓄積し、Planner のコレクション優先順位・rag/web 振り分けの事前分布に反映する。

- 永続化: JSONL（既定 logs/grace_memory.jsonl）。1行=1実行レコード。
- 集計: コレクションごとに success_rate × mean_confidence を
  Laplace スムージング付きで算出し、優先順位を返す。
- キーワード一致でフィルタして「この種の質問で当たりやすいコレクション」を推定する。

外部依存なし・決定的。ユニットテスト可能。
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_PATH = "logs/grace_memory.jsonl"

# 英数字・漢字・カタカナの 2 文字以上の連続をキーワードとして抽出する。
# **ひらがなは語の区切りとして扱い、語に含めない。**
#
# ⚠️ 以前は `[一-鿿゠-ヿ぀-ゟ]{2,}`（漢字・カナ・かなの連続）だったため、
#    「住民票の写しの取り方は」が丸ごと 1 語になり、言い回しが少し違うだけで
#    重ならなかった（「住民票の写しを取りたい」とは 1 語も一致しない）。
#    日本語の内容語はおおむね漢字かカタカナで書かれ、助詞・活用語尾はひらがな
#    なので、ひらがなで区切れば形態素解析なしでも内容語がほぼ取り出せる
#    （「明日の東京の天気は？」→ 明日 / 東京 / 天気）。
#    ひらがなだけの語（「ごみ」など）と 1 文字の漢字語は落ちる。
_KEYWORD_RE = re.compile(r"[a-z0-9]{2,}|[一-鿿々〆]{2,}|[ァ-ヺー]{2,}")


def extract_keywords(text: str, top_n: int = 8) -> list[str]:
    """軽量なキーワード抽出（形態素解析非依存・決定的）。

    NFKC で全角英数・半角カナを揃えてから小文字にし、英数字・漢字・カタカナの
    連続（2 文字以上）を出現順に最大 `top_n` 個返す。ひらがなは区切りとして捨てる。
    """
    if not text:
        return []
    seen: list[str] = []
    for m in _KEYWORD_RE.findall(unicodedata.normalize("NFKC", text).lower()):
        t = m.lower()
        if t not in seen:
            seen.append(t)
        if len(seen) >= top_n:
            break
    return seen


@dataclass
class MemoryRecord:
    query: str
    keywords: list[str]
    collection: Optional[str]
    success: bool
    confidence: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "keywords": self.keywords,
            "collection": self.collection,
            "success": self.success,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryRecord":
        return cls(
            query=d.get("query", ""),
            keywords=list(d.get("keywords", []) or []),
            collection=d.get("collection"),
            success=bool(d.get("success", False)),
            confidence=float(d.get("confidence", 0.0) or 0.0),
            timestamp=float(d.get("timestamp", 0.0) or 0.0),
        )


def _record_keywords(record: MemoryRecord) -> set[str]:
    """照合に使うレコードのキーワード。`query` があれば今の規則で抽出し直す。

    記録済みの `keywords` は**書いた時点の抽出規則**の結果である。2026-10-15 に
    規則を変えた（ひらがなを区切りにした）ので、それ以前のレコードは
    「住民票の写しの取り方は」のような文節を持ち、新しい規則の「住民票」と一致しない。
    `query` が空のレコード（手で書いたもの等）だけ記録済みの値を使う。
    """
    if record.query:
        return set(extract_keywords(record.query))
    return set(record.keywords)


@dataclass
class CollectionStat:
    collection: str
    count: int
    success_count: int
    mean_confidence: float

    @property
    def success_rate(self) -> float:
        return self.success_count / self.count if self.count else 0.0

    def score(self, alpha: float = 1.0, beta: float = 1.0) -> float:
        """success_rate（Laplace 平滑化）× mean_confidence。"""
        smoothed_sr = (self.success_count + alpha) / (self.count + alpha + beta)
        return smoothed_sr * self.mean_confidence


class ExecutionMemory:
    """実行レコードの蓄積と、コレクション事前分布の算出。"""

    def __init__(self, path: str = DEFAULT_MEMORY_PATH):
        self.path = Path(path)

    # --- 記録 ---
    def record(self, query: str, collection: Optional[str], success: bool,
               confidence: float, keywords: Optional[list[str]] = None) -> None:
        """1実行レコードを JSONL へ追記する（best-effort）。"""
        rec = MemoryRecord(
            query=query or "",
            keywords=keywords if keywords is not None else extract_keywords(query or ""),
            collection=collection,
            success=bool(success),
            confidence=float(confidence or 0.0),
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:  # 記録失敗は実行を止めない
            logger.warning(f"ExecutionMemory.record failed: {e}")

    def record_many(self, query: str, collections: list[Optional[str]], success: bool,
                    confidence: float, keywords: Optional[list[str]] = None) -> None:
        """複数コレクションを使った実行をまとめて記録する。"""
        kw = keywords if keywords is not None else extract_keywords(query or "")
        seen = set()
        for c in collections:
            if c in seen:
                continue
            seen.add(c)
            self.record(query, c, success, confidence, keywords=kw)

    # --- 読み込み ---
    def load(self) -> list[MemoryRecord]:
        if not self.path.exists():
            return []
        records: list[MemoryRecord] = []
        skipped = 0
        try:
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # 1 行の破損で全件を捨てない。JSONL は追記専用のため、
                    # マージコンフリクトのマーカー混入・書き込み中断・手編集ミスで
                    # 壊れた行が混ざりうる。壊れた行だけ飛ばして残りを活かす
                    # （以前は try が for 全体を囲んでおり、1 行目以外の破損でも
                    #  それ以降が丸ごと失われていた）。
                    try:
                        records.append(MemoryRecord.from_dict(json.loads(line)))
                    except Exception:
                        skipped += 1
        except OSError as e:
            logger.warning(f"ExecutionMemory.load failed: {e}")
        if skipped:
            logger.warning(
                f"ExecutionMemory.load skipped {skipped} malformed line(s) "
                f"in {self.path} (loaded {len(records)})"
            )
        return records

    # --- 集計 ---
    def collection_priors(
        self,
        query: Optional[str] = None,
        min_keyword_overlap: int = 1,
    ) -> list[CollectionStat]:
        """コレクション事前分布を score 降順で返す。

        query 指定時はキーワードが overlap するレコードのみを対象にする
        （その種の質問に対する分布）。**overlap レコードが無ければ空を返す**
        （＝ `best_collection` は None ＝ 全コレクション検索）。
        query を渡さないときだけ全レコードを集計する。

        ⚠️ **重なりが無いときに全体集計へ倒さないこと。** 以前はそうしていたため、
        実績のいちばん多いコレクションが**どんな新しい質問にも**返っていた。
        実測（2026-10-15）: 49ers の人事の質問（cc_news の話題）に
        `ec_policy_anthropic`（返品・領収書ポリシー）が付き、rag_search の先頭で
        検索されていた。RAGSearchTool は一次しきい値を超えた最初のコレクションで
        打ち切るので、そこで当たると正解のあるコレクションは検索されない。
        2026-08-17 の「天気の質問に wikipedia」も同じ仕組みで起きていた。

        レコードの照合には、記録済みの `keywords` ではなく **`query` から抽出し直した
        キーワード**を使う（`_record_keywords`）。抽出規則を変えても、既存の
        `logs/grace_memory.jsonl` をそのまま使えるようにするため。
        """
        records = self.load()
        if not records:
            return []

        if query:
            target_kw = set(extract_keywords(query))
            filtered = [
                r for r in records
                if r.collection
                and len(target_kw & _record_keywords(r)) >= min_keyword_overlap
            ]
        else:
            filtered = [r for r in records if r.collection]

        agg: dict[str, dict] = {}
        for r in filtered:
            a = agg.setdefault(r.collection, {"count": 0, "succ": 0, "conf_sum": 0.0})
            a["count"] += 1
            a["succ"] += 1 if r.success else 0
            a["conf_sum"] += r.confidence

        stats = [
            CollectionStat(
                collection=c,
                count=a["count"],
                success_count=a["succ"],
                mean_confidence=(a["conf_sum"] / a["count"] if a["count"] else 0.0),
            )
            for c, a in agg.items()
        ]
        stats.sort(key=lambda s: s.score(), reverse=True)
        return stats

    def best_collection(
        self,
        query: Optional[str] = None,
        min_count: int = 3,
        min_score: float = 0.6,
        exclude: Optional[Callable[[str], bool]] = None,
    ) -> Optional[str]:
        """十分な実績（count>=min_count かつ score>=min_score）がある
        最良コレクションを返す。条件を満たすものが無ければ None（=全コレクション検索）。

        Args:
            exclude: True を返したコレクションを候補から外す述語。

                ⚠️ **除外対象は「飛ばして次点を採る」。諦めて None を返さない。**

                過去に誤って採用されたコレクションが success として記録されると、
                それが常に最上位に居座る。除外対象に当たった時点で None を返すと
                **メモリ機構そのものが事実上死ぬ**（毎回 None ＝ 全コレクション検索）。

                `collection_priors` は score 降順なので、除外分を読み飛ばせば
                正当な次点（gov_faq 等）を拾える。この設計により、**古い誤学習
                レコードを消さなくても無害になる**（`logs/grace_memory.jsonl` の
                削除は不可逆なので、運用者に強いたくない）。
        """
        for stat in self.collection_priors(query=query):
            if stat.count < min_count or stat.score() < min_score:
                continue
            if exclude is not None and exclude(stat.collection):
                logger.debug(
                    "[memory] %s は除外対象のため次点を探す（score=%.3f）",
                    stat.collection, stat.score(),
                )
                continue
            return stat.collection
        return None


def create_execution_memory(path: str = DEFAULT_MEMORY_PATH) -> ExecutionMemory:
    return ExecutionMemory(path=path)
