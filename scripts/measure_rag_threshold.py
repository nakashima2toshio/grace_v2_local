#!/usr/bin/env python3
"""RAG 採用閾値（`executor.reasoning_min_rag_score`）を **実測で決める** ための計測スクリプト。

## なぜ必要か

現在の既定は 0.55 だが、これは根拠のある値ではない。実測（「明日の東京の天気は？」）
では、社内ナレッジに該当が 1 件も無い質問なのに緩和検索の最良スコアが 0.6658 あり、
0.55 では**無関係な文書が出典として採用されてしまう**。

かといって闇雲に上げると、本当に社内ナレッジで答えられる質問まで出典 0 件になる。
**「拾うべき質問の最低スコア」と「拾ってはいけない質問の最高スコア」の間**に
閾値を置く必要があり、その 2 つは測らないと分からない。

## 使い方

    # 前提: Qdrant 起動済み・Embedding が使える（.env 設定済み）
    PYTHONPATH=. python3 scripts/measure_rag_threshold.py --vertical gov
    PYTHONPATH=. python3 scripts/measure_rag_threshold.py --vertical all

    # 質問を自分で用意する場合（推奨: 実際に来る質問を入れる）
    PYTHONPATH=. python3 scripts/measure_rag_threshold.py --queries-file myqueries.json

`--queries-file` の形式:

    {
      "in_scope":  ["住民票の写しの取り方は？", "..."],
      "out_of_scope": ["明日の東京の天気は？", "..."]
    }

- `in_scope`     … 社内ナレッジで**答えられるべき**質問（＝拾いたい）
- `out_of_scope` … 社内ナレッジに**無い**話題（＝拾ってはいけない）

## 出力の読み方

    in_scope  の最小 Top スコア  = TP フロア（これ未満にすると取りこぼす）
    out_scope の最大 Top スコア  = FP シーリング（これ以下にすると誤採用する）

    FP シーリング < TP フロア  → その中間が安全な閾値。推奨値を出す。
    FP シーリング >= TP フロア → **スコアだけでは分離できない**。閾値調整では
                                 解決しないので、Embedding モデル・チャンク粒度・
                                 コレクション分割の側を見直す。

得られた値は `config/grace_config.yml` の `executor.reasoning_min_rag_score`
に反映する（コード変更は不要）。

## 検索対象は本番と同じ

候補一覧は `RAGSearchTool._get_all_collections_dynamic()` をそのまま呼んで作る。
次元不一致・空・`qdrant.excluded_collections`（汎用コーパス）はここで落ちる。

⚠️ **測定条件を本番に合わせることが要点。** 以前は Qdrant の全コレクションを
素で舐めていたため、768 次元のコレクションへ 3072 次元のクエリを投げて
1 実行あたり 272 回の `400 Bad Request` を出していた。本番では検索されない
コレクションのスコアを測っていたことにもなる。

`--include-excluded` を付けると除外を無効にして測れる（除外の効果を
before / after で比べたいとき用）。

⚠️ このスクリプトは読み取りのみ。Qdrant に書き込まない。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 既定の質問セット
#
# ⚠️ あくまで**出発点**。実運用の質問ログに置き換えて測り直すこと。
#    out_of_scope は「社内ナレッジに絶対に無い」ものを選ぶ（天気・株価・
#    その日のニュースなど）。ここが甘いと FP シーリングが低く出て、
#    閾値を過小評価する。
# ---------------------------------------------------------------------------
DEFAULT_IN_SCOPE: Dict[str, List[str]] = {
    "gov": [
        "住民票の写しの取り方は？",
        "転入届の提出期限はいつまでですか？",
        "印鑑登録に必要な持ち物を教えてください",
        "国民健康保険の加入手続きはどこでできますか？",
    ],
    "saas": [
        "API のレート制限はどれくらいですか？",
        "アクセストークンの有効期限は？",
        "Webhook の再送仕様を教えてください",
        "SSO の設定手順は？",
    ],
    "ec": [
        "返品はいつまで受け付けていますか？",
        "注文のキャンセルはどうすればいいですか？",
        "送料が無料になる条件は？",
        "領収書は発行できますか？",
    ],
}

# どの業界でも社内ナレッジに存在しない質問（＝拾ってはいけない）
DEFAULT_OUT_OF_SCOPE: List[str] = [
    "明日の東京の天気は？",
    "今日の日経平均株価はいくらですか？",
    "近くのおいしいラーメン屋を教えて",
    "今年のノーベル物理学賞は誰ですか？",
    "円ドル相場の見通しは？",
]


def _collections_for(vertical: str) -> Optional[List[str]]:
    """業界プロファイルの対象コレクション。`all` なら None（全コレクション横断）。"""
    if vertical == "all":
        return None
    from backend.app.core.verticals import PROFILES

    profile = PROFILES.get(vertical)
    if profile is None:
        raise SystemExit(f"unknown vertical: {vertical} (choices: {', '.join(PROFILES)}, all)")
    return list(profile.collections)


def _searchable_collections(apply_exclusions: bool = True) -> List[str]:
    """本番と**同じ**候補一覧を返す。

    ⚠️ 以前ここは Qdrant の全コレクションを素で返していた。本体
    （`RAGSearchTool._get_all_collections_dynamic`）は次元不一致・空・除外設定を
    落としてから検索するので、**測定条件が本番と違っていた**。実害:

      - `_ollama` 系 8 コレクション（768 次元）に 3072 次元のクエリを投げ、
        1 実行で 272 回の `400 Bad Request` が出てログが読めなくなった
        （`Vector dimension error: expected dim: 768, got 3072`）
      - 除外設定（汎用コーパス）を無視するため、本番では検索されない
        コレクションのスコアを測ってしまう

    本体のロジックをそのまま呼ぶことで、条件のずれを構造的に無くす。
    """
    from grace.config import get_config
    from grace.tools import RAGSearchTool

    tool = RAGSearchTool(config=get_config())
    return tool._get_all_collections_dynamic(apply_exclusions=apply_exclusions)


def _all_registered_collections() -> List[str]:
    """Qdrant に登録されている全コレクション（未登録判定の表示用）。"""
    from qdrant_client_wrapper import get_qdrant_client

    client = get_qdrant_client()
    return sorted(c.name for c in client.get_collections().collections)


def _top_score(query: str, collection: str) -> Optional[float]:
    """1 コレクションを検索して Top スコアを返す（0 件なら None）。"""
    from agent_tools import search_rag_knowledge_base_structured

    try:
        results = search_rag_knowledge_base_structured(query, collection)
    except Exception as e:  # 計測は 1 コレクションの失敗で止めない
        print(f"    ! {collection}: 検索失敗 {type(e).__name__}: {e}", file=sys.stderr)
        return None

    if isinstance(results, str) or not results:
        return None
    return max(float(r.get("score", 0.0)) for r in results)


def measure(queries: List[str], collections: List[str]) -> List[Tuple[str, Optional[float], str]]:
    """各質問の「全コレクション中の最良スコア」を測る。

    ⚠️ **最良**で測るのが要点。`RAGSearchTool` は緩和時に最高スコアの
    コレクションを採用するので、採用是非を決めるのはこの値になる。
    """
    rows: List[Tuple[str, Optional[float], str]] = []
    for query in queries:
        best_score: Optional[float] = None
        best_collection = "-"
        for collection in collections:
            score = _top_score(query, collection)
            if score is not None and (best_score is None or score > best_score):
                best_score, best_collection = score, collection
        rows.append((query, best_score, best_collection))
        shown = f"{best_score:.4f}" if best_score is not None else "  なし"
        print(f"  {shown}  {best_collection:<28} {query}")
    return rows


def _scores(rows) -> List[float]:
    return [s for _, s, _ in rows if s is not None]


def report(in_rows, out_rows) -> int:
    """分離可否を判定して推奨閾値を出す。戻り値は終了コード。"""
    in_scores, out_scores = _scores(in_rows), _scores(out_rows)

    print("\n" + "=" * 72)
    print("集計")
    print("=" * 72)

    if not in_scores or not out_scores:
        print("！ スコアが取れた質問が足りない（Qdrant にデータが登録されているか確認）")
        return 2

    tp_floor = min(in_scores)
    fp_ceiling = max(out_scores)

    print(f"  in_scope  : n={len(in_scores)} 最小={tp_floor:.4f} "
          f"中央={statistics.median(in_scores):.4f} 最大={max(in_scores):.4f}")
    print(f"  out_scope : n={len(out_scores)} 最小={min(out_scores):.4f} "
          f"中央={statistics.median(out_scores):.4f} 最大={fp_ceiling:.4f}")
    print()
    print(f"  TP フロア（これ未満にすると取りこぼす）  : {tp_floor:.4f}")
    print(f"  FP シーリング（これ以下だと誤採用する）  : {fp_ceiling:.4f}")
    print()

    if fp_ceiling >= tp_floor:
        print("  ✗ 分離できない。閾値をどこに置いても、取りこぼすか誤採用するかになる。")
        print("    → 閾値調整では解決しない。Embedding モデル・チャンク粒度・")
        print("      コレクション分割を見直すこと。")
        worst = min(in_rows, key=lambda r: (r[1] is None, r[1] if r[1] is not None else 0))
        best_fp = max(out_rows, key=lambda r: (r[1] is not None, r[1] if r[1] is not None else 0))
        print(f"    最も低い in_scope : {worst[1]} … {worst[0]}")
        print(f"    最も高い out_scope: {best_fp[1]} … {best_fp[0]}  ({best_fp[2]})")
        return 1

    recommended = round((tp_floor + fp_ceiling) / 2, 2)
    print(f"  ✓ 分離できる。推奨閾値 = {recommended:.2f}"
          f"（{fp_ceiling:.4f} 〜 {tp_floor:.4f} の中間）")
    print()
    print("  反映先: config/grace_config.yml")
    print(f"    executor:\n      reasoning_min_rag_score: {recommended:.2f}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vertical", default="all",
                        help="gov / saas / ec / all（既定: all）")
    parser.add_argument("--queries-file", type=Path,
                        help="in_scope / out_of_scope を書いた JSON")
    parser.add_argument("--include-excluded", action="store_true",
                        help="qdrant.excluded_collections（汎用コーパス）も測る。"
                             "除外の効果を before/after で見たいときに使う")
    args = parser.parse_args(argv)

    if args.queries_file:
        payload = json.loads(args.queries_file.read_text(encoding="utf-8"))
        in_queries = list(payload.get("in_scope") or [])
        out_queries = list(payload.get("out_of_scope") or [])
    elif args.vertical == "all":
        in_queries = [q for qs in DEFAULT_IN_SCOPE.values() for q in qs]
        out_queries = list(DEFAULT_OUT_OF_SCOPE)
    else:
        in_queries = list(DEFAULT_IN_SCOPE.get(args.vertical, []))
        out_queries = list(DEFAULT_OUT_OF_SCOPE)

    # ⚠️ 本番と同じ候補一覧を使う（次元不一致・空・除外設定を落とす）。
    #    業界プロファイル指定時は本番も除外を重ねないので、ここも合わせる。
    profile_collections = _collections_for(args.vertical)
    searchable = _searchable_collections(
        apply_exclusions=(profile_collections is None and not args.include_excluded)
    )

    if profile_collections is None:
        collections = searchable
    else:
        # プロファイルは部分一致で書かれる（"wikipedia_ja" → "wikipedia_ja_5per"）
        collections = [c for c in searchable if any(k in c for k in profile_collections)]
        unmatched = [k for k in profile_collections
                     if not any(k in c for c in searchable)]
        if unmatched:
            print(f"（検索可能な実体が無いためスキップ: {', '.join(unmatched)}）")

    if not collections:
        raise SystemExit("検索対象のコレクションが 1 つも無い")

    registered = _all_registered_collections()
    dropped = [c for c in registered if c not in collections]
    print(f"対象コレクション ({len(collections)}/{len(registered)}): {', '.join(collections)}")
    if dropped:
        print(f"対象外 ({len(dropped)}): {', '.join(dropped)}")
        print("  ※ 次元不一致・空・qdrant.excluded_collections・プロファイル範囲外")
    print()

    print("in_scope（拾いたい）")
    in_rows = measure(in_queries, collections)
    print("\nout_of_scope（拾ってはいけない）")
    out_rows = measure(out_queries, collections)

    return report(in_rows, out_rows)


if __name__ == "__main__":
    raise SystemExit(main())
