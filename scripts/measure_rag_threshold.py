#!/usr/bin/env python3
"""RAG のしきい値（採用の下限・Web 検索の要否）を **実測で決める** ための計測スクリプト。

## なぜ必要か

GRACE-Support の executor は、RAG の最高スコアで次の 2 つを決める。

| しきい値 | 既定 | 意味 |
|---|---|---|
| `executor.reasoning_min_rag_score` | 0.64 | これ以上の結果だけを推論に使い、出典に載せる（採用の下限） |
| `qdrant.rag_sufficient_score` | 0.64 | これ未満なら Web 検索を差し込む。以上なら LLM の適合性チェックが決める。採用の下限以下にする |

闇雲に上げると社内ナレッジで答えられる質問まで取りこぼし、下げると無関係な文書を
出典として採用してしまう。**「拾うべき質問の最低スコア」と「拾ってはいけない質問の最高スコア」の間**に
置く必要があり、その 2 つは測らないと分からない。

**LLM は呼ばない**（Gemini Embedding と Qdrant だけ。読み取りのみで Qdrant に書き込まない）。

> 2026-10-05 に、grace_v2 の `scripts/measure_rag_scores.py`（業界ごとの集計・Web 検索の要否の帯）を
> このスクリプトへ統合した。grace_v2 と grace_v2_local に**同じ内容**で置く（Qdrant と Embedding が共用なので
> スコアは同じ。しきい値は各リポジトリの `config/grace_config.yml` から読む）。

## 使い方

    # 前提: Qdrant 起動済み・Embedding が使える（.env 設定済み）
    PYTHONPATH=. python3 scripts/measure_rag_threshold.py                  # = --vertical all
    PYTHONPATH=. python3 scripts/measure_rag_threshold.py --vertical gov   # 1 業界
    PYTHONPATH=. python3 scripts/measure_rag_threshold.py --vertical each  # gov / saas / ec を順に

    # 質問を自分で用意する場合（推奨: 実際に来る質問を入れる）
    PYTHONPATH=. python3 scripts/measure_rag_threshold.py --queries-file myqueries.json

| `--vertical` | 検索するコレクション | 範囲内の質問 | 範囲外の質問 |
|---|---|---|---|
| `all`（既定） | Qdrant 上の検索可能な全コレクション（汎用コーパスは除外。業界指定なしの「基本版」と同じ） | 全業界の既定の質問 | 共通の範囲外 |
| `gov` / `saas` / `ec` | その業界プロファイルの許可コレクション | その業界の質問 | 共通の範囲外 ＋ **他業界の質問** |
| `each` | 上を 3 業界ぶん順に | 同上 | 同上 |

他業界の質問を範囲外に入れるのは、それが本番でいちばん起きやすい誤採用だから
（実測 2026-10-05: saas で「返品したい」が 0.6707 で採用の下限を超えた）。

`--queries-file` の形式（どちらか）:

    {"in_scope": ["住民票の写しの取り方は？"], "out_of_scope": ["明日の東京の天気は？"]}
    {"gov": {"in_scope": [...], "out_of_scope": [...]}, "saas": {...}}   # 業界ごと（each / 業界指定で使う）

## 出力の読み方

    in_scope  の最小 Top スコア  = TP フロア（これ未満にすると取りこぼす）
    out_scope の最大 Top スコア  = FP シーリング（これ以下にすると誤採用する）

    FP シーリング < TP フロア  → その中間が安全な閾値。推奨値を出す（終了コード 0）。
    FP シーリング >= TP フロア → **スコアだけでは分離できない**（終了コード 1）。閾値調整では
                                 解決しないので、Embedding モデル・チャンク粒度・
                                 コレクション分割の側を見直すか、採用後の LLM 判定に任せる。

あわせて、**今のしきい値**で各質問がどの帯に入るかを数える:

| 帯 | 条件 | executor の振る舞い |
|---|---|---|
| 不採用 | スコア < 採用の下限 | 0 件扱い → Web へ（範囲内の質問なら取りこぼし） |
| 強制 Web | 採用の下限 ≤ スコア < Web 検索の要否 | 採用するのに無条件で Web も検索する（2 つのしきい値が食い違うときだけ起きる） |
| 適合性チェック | Web 検索の要否 ≤ スコア | 採用し、LLM の適合性チェックが Web の要否を決める |

結果は `logs/rag_scores/rag_scores_<日時>.json` にも書く。得られた値は `config/grace_config.yml` の
`executor.reasoning_min_rag_score`（と `qdrant.rag_sufficient_score`）に反映する（コード変更は不要）。

⚠️ **範囲内の質問は、本当に社内ナレッジに答えがあるものだけにする。** データに無い質問を範囲内に
入れると TP フロアが下がり、閾値を過小評価する。範囲外は「社内ナレッジに絶対に無い」もの
（天気・株価・その日のニュースなど）を選ぶ。既定の質問は**出発点**で、実運用の質問ログで測り直すこと。

## 検索対象は本番と同じ

候補一覧は `RAGSearchTool._get_all_collections_dynamic()` をそのまま呼んで作り、
検索は executor の RAG ツールと同じ `agent_tools.search_rag_knowledge_base_structured()` で行う
（sparse が使えれば hybrid。緩和閾値 0.5 にも届かないものは「なし」）。
次元不一致・空・`qdrant.excluded_collections`（汎用コーパス）はここで落ちる。

⚠️ **測定条件を本番に合わせることが要点。** 以前は Qdrant の全コレクションを
素で舐めていたため、768 次元のコレクションへ 3072 次元のクエリを投げて
1 実行あたり 272 回の `400 Bad Request` を出していた。本番では検索されない
コレクションのスコアを測っていたことにもなる。

`--include-excluded` を付けると除外を無効にして測れる（除外の効果を
before / after で比べたいとき用）。
"""
from __future__ import annotations

import argparse
import inspect
import json
import statistics
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERTICALS = ("gov", "saas", "ec")

# ---------------------------------------------------------------------------
# 既定の質問セット
#
# ⚠️ あくまで**出発点**。実運用の質問ログに置き換えて測り直すこと。
#    前半（画面の例文とその言い換え）は、2026-10-04 の Mac の E2E の回答に実際に出てきた
#    社内ナレッジの内容から作った（答えがあることを確かめてある）。後半は以前からの質問で、
#    サンプルデータ（各 10 点前後）に答えがあるかは確かめていない。
# ---------------------------------------------------------------------------
DEFAULT_IN_SCOPE: Dict[str, List[str]] = {
    "gov": [
        "住民票の写しの取り方は？",
        "住民票の写しの手数料はいくらですか",
        "コンビニで住民票を取得できますか",
        "住民票を郵送で請求するときの支払い方法は？",
        "転入届の提出期限はいつまでですか？",
        "印鑑登録に必要な持ち物を教えてください",
        "国民健康保険の加入手続きはどこでできますか？",
    ],
    "saas": [
        "サービスが落ちています",
        "障害情報はどこで確認できますか",
        "計画メンテナンスはいつ通知されますか",
        "Enterprise プランの稼働率保証は？",
        "サポート窓口の受付時間は？",
        "API のレート制限はどれくらいですか？",
        "アクセストークンの有効期限は？",
        "Webhook の再送仕様を教えてください",
        "SSO の設定手順は？",
    ],
    "ec": [
        "返品したい",
        "返品できる期限は何日ですか",
        "不良品が届いたらどうすればいいですか",
        "返金はいつされますか",
        "発送前の注文はキャンセルできますか",
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
    "カレーの作り方を教えて",
    "おすすめの映画を教えて",
]

Row = Tuple[str, Optional[float], str]   # (query, 最良スコア or None, コレクション)


# ---------------------------------------------------------------------------
# 検索
# ---------------------------------------------------------------------------
def _collections_for(vertical: str) -> Optional[List[str]]:
    """業界プロファイルの対象コレクション。`all` なら None（全コレクション横断）。"""
    if vertical == "all":
        return None
    from backend.app.core.verticals import PROFILES

    profile = PROFILES.get(vertical)
    if profile is None:
        raise SystemExit(f"unknown vertical: {vertical} (choices: {', '.join(PROFILES)}, all, each)")
    return list(profile.collections)


def _searchable_collections(apply_exclusions: bool = True) -> List[str]:
    """本番と**同じ**候補一覧を返す（次元不一致・空・除外設定を落とす）。

    ⚠️ 以前ここは Qdrant の全コレクションを素で返していた。本体
    （`RAGSearchTool._get_all_collections_dynamic`）は次元不一致・空・除外設定を
    落としてから検索するので、**測定条件が本番と違っていた**。実害:

      - `_ollama` 系 8 コレクション（768 次元）に 3072 次元のクエリを投げ、
        1 実行で 272 回の `400 Bad Request` が出てログが読めなくなった
        （`Vector dimension error: expected dim: 768, got 3072`）
      - 除外設定（汎用コーパス）を無視するため、本番では検索されない
        コレクションのスコアを測ってしまう

    本体のロジックをそのまま呼ぶことで、条件のずれを構造的に無くす。
    grace_v2_local は `_get_all_collections_dynamic(apply_exclusions=...)` で除外まで行い、
    grace_v2 は除外を `_apply_excluded_collections()` で別に行うので、両方に対応する。
    """
    from grace.config import get_config
    from grace.tools import RAGSearchTool

    tool = RAGSearchTool(config=get_config())
    getter = tool._get_all_collections_dynamic
    if "apply_exclusions" in inspect.signature(getter).parameters:
        return list(getter(apply_exclusions=apply_exclusions) or [])
    names = list(getter() or [])
    if apply_exclusions and hasattr(tool, "_apply_excluded_collections"):
        names = tool._apply_excluded_collections(names)
    return names


def _all_registered_collections() -> List[str]:
    """Qdrant に登録されている全コレクション（未登録判定の表示用）。"""
    from qdrant_client_wrapper import get_qdrant_client

    client = get_qdrant_client()
    return sorted(c.name for c in client.get_collections().collections)


@lru_cache(maxsize=None)
def _query_vectors(query: str):
    """質問の dense / sparse ベクトル（コレクションごとに Embedding し直さない）。"""
    from qdrant_client_wrapper import embed_query, embed_sparse_query_unified

    try:
        sparse = embed_sparse_query_unified(query)
    except Exception:
        sparse = None      # 取れなければ dense だけ（アプリも同じく倒れる）
    return embed_query(query), sparse


def _top_score(query: str, collection: str) -> Optional[float]:
    """1 コレクションを検索して Top スコアを返す（0 件・緩和閾値未満なら None）。"""
    from agent_tools import search_rag_knowledge_base_structured

    try:
        dense, sparse = _query_vectors(query)
        results = search_rag_knowledge_base_structured(
            query, collection,
            precomputed_query_vector=dense, precomputed_sparse_vector=sparse,
        )
    except Exception as e:  # 計測は 1 コレクションの失敗で止めない
        print(f"    ! {collection}: 検索失敗 {type(e).__name__}: {e}", file=sys.stderr)
        return None

    if isinstance(results, str) or not results:
        return None
    return max(float(r.get("score", 0.0)) for r in results)


def measure(queries: List[str], collections: List[str]) -> List[Row]:
    """各質問の「全コレクション中の最良スコア」を測る。

    ⚠️ **最良**で測るのが要点。`RAGSearchTool` は緩和時に最高スコアの
    コレクションを採用するので、採用是非を決めるのはこの値になる。
    """
    rows: List[Row] = []
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


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
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


def zone(score: Optional[float], adopt: float, sufficient: float) -> str:
    """executor から見たスコアの帯（モジュールの docstring の表）。None は不採用。"""
    if score is None or score < adopt:
        return "rejected"
    if score < sufficient:
        return "forced_web"
    return "relevance"


def _r(x: float) -> float:
    return round(float(x), 4)


def analyze(rows: List[Dict], adopt: float, sufficient: float) -> Dict[str, Dict]:
    """業界ごと（と全体）に、範囲内・範囲外のスコア分布と、今のしきい値での振る舞いを集計する。

    `rows` は `{"vertical", "label"（in / out）, "query", "top"（None 可）, "collection"}`。
    """
    groups: Dict[str, List[Dict]] = {"(all)": rows}
    for row in rows:
        groups.setdefault(row["vertical"], []).append(row)

    out: Dict[str, Dict] = {}
    for name, items in groups.items():
        ins = [r["top"] for r in items if r["label"] == "in"]
        outs = [r["top"] for r in items if r["label"] == "out"]
        in_s = sorted(s for s in ins if s is not None)
        out_s = sorted(s for s in outs if s is not None)
        entry: Dict[str, object] = {
            "in": {"n": len(ins), "min": _r(in_s[0]) if in_s else None,
                   "median": _r(statistics.median(in_s)) if in_s else None,
                   "max": _r(in_s[-1]) if in_s else None},
            "out": {"n": len(outs), "max": _r(out_s[-1]) if out_s else None},
            # 範囲内の質問が採用されない／採用されるのに Web も検索する件数
            "in_rejected": sum(zone(s, adopt, sufficient) == "rejected" for s in ins),
            "in_forced_web": sum(zone(s, adopt, sufficient) == "forced_web" for s in ins),
            # 範囲外の質問が採用されてしまう件数（無関係な社内文書を根拠にする）
            "out_adopted": sum(zone(s, adopt, sufficient) != "rejected" for s in outs),
        }
        if in_s and out_s:
            entry["margin"] = _r(in_s[0] - out_s[-1])
            # 範囲内の最小と範囲外の最大の中点（幅が正のときだけ意味がある）
            entry["midpoint"] = _r((in_s[0] + out_s[-1]) / 2) if in_s[0] > out_s[-1] else None
        out[name] = entry
    return out


# ---------------------------------------------------------------------------
# 入力（質問セット）
# ---------------------------------------------------------------------------
def queries_for(vertical: str, payload: Optional[Dict] = None) -> Tuple[List[str], List[str]]:
    """(範囲内, 範囲外) の質問。業界指定では、他業界の範囲内の質問も範囲外に入れる。"""
    if payload is not None:
        section = payload.get(vertical) if vertical in payload else payload
        if not isinstance(section, dict) or not ({"in_scope", "out_of_scope"} & set(section)):
            raise SystemExit(f"--queries-file に {vertical} の in_scope / out_of_scope が無い")
        return list(section.get("in_scope") or []), list(section.get("out_of_scope") or [])
    if vertical == "all":
        return [q for v in VERTICALS for q in DEFAULT_IN_SCOPE[v]], list(DEFAULT_OUT_OF_SCOPE)
    others = [q for v in VERTICALS if v != vertical for q in DEFAULT_IN_SCOPE[v]]
    return list(DEFAULT_IN_SCOPE.get(vertical, [])), list(DEFAULT_OUT_OF_SCOPE) + others


def _target_collections(vertical: str, include_excluded: bool) -> List[str]:
    # ⚠️ 本番と同じ候補一覧を使う（次元不一致・空・除外設定を落とす）。
    #    業界プロファイル指定時は本番も除外を重ねないので、ここも合わせる。
    profile_collections = _collections_for(vertical)
    searchable = _searchable_collections(
        apply_exclusions=(profile_collections is None and not include_excluded)
    )
    if profile_collections is None:
        collections = searchable
    else:
        # プロファイルは部分一致で書かれる（"wikipedia_ja" → "wikipedia_ja_5per"）
        collections = [c for c in searchable if any(k in c for k in profile_collections)]
        unmatched = [k for k in profile_collections if not any(k in c for c in searchable)]
        if unmatched:
            print(f"（検索可能な実体が無いためスキップ: {', '.join(unmatched)}）")
    if not collections:
        raise SystemExit(f"検索対象のコレクションが 1 つも無い（{vertical}）")

    registered = _all_registered_collections()
    dropped = [c for c in registered if c not in collections]
    print(f"対象コレクション ({len(collections)}/{len(registered)}): {', '.join(collections)}")
    if dropped:
        print(f"対象外 ({len(dropped)}): {', '.join(dropped)}")
        print("  ※ 次元不一致・空・qdrant.excluded_collections・プロファイル範囲外")
    print()
    return collections


def _print_summary(summary: Dict[str, Dict], adopt: float, sufficient: float) -> None:
    print(f"\n今のしきい値（採用の下限 {adopt} / Web 検索の要否 {sufficient}）での振る舞い")
    print("業界     範囲内(n・最小・中央)        範囲外(n・最大)   幅       中点     範囲内:不採用/強制Web  範囲外:採用")
    for name, s in summary.items():
        i, o = s["in"], s["out"]
        print(f"{name:8} {i['n']:2}・{i['min']}・{i['median']}   {o['n']:2}・{o['max']}   "
              f"{s.get('margin')}   {s.get('midpoint')}   {s['in_rejected']}/{s['in_forced_web']}   {s['out_adopted']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vertical", default="all",
                        help="all（既定）/ gov / saas / ec / each（3 業界を順に）")
    parser.add_argument("--queries-file", type=Path,
                        help="in_scope / out_of_scope を書いた JSON（業界ごとの形も可）")
    parser.add_argument("--include-excluded", action="store_true",
                        help="qdrant.excluded_collections（汎用コーパス）も測る。"
                             "除外の効果を before/after で見たいときに使う")
    parser.add_argument("--out", type=Path, default=None,
                        help="結果 JSON（既定: logs/rag_scores/rag_scores_<日時>.json）")
    args = parser.parse_args(argv)

    payload = json.loads(args.queries_file.read_text(encoding="utf-8")) if args.queries_file else None
    verticals = list(VERTICALS) if args.vertical == "each" else [args.vertical]

    rows: List[Dict] = []
    in_rows: List[Row] = []
    out_rows: List[Row] = []
    for vertical in verticals:
        if len(verticals) > 1:
            print(f"\n{'#' * 72}\n# {vertical}\n{'#' * 72}")
        in_queries, out_queries = queries_for(vertical, payload)
        collections = _target_collections(vertical, args.include_excluded)
        print("in_scope（拾いたい）")
        v_in = measure(in_queries, collections)
        print("\nout_of_scope（拾ってはいけない）")
        v_out = measure(out_queries, collections)
        in_rows += v_in
        out_rows += v_out
        rows += [{"vertical": vertical, "label": "in", "query": q, "top": s, "collection": c} for q, s, c in v_in]
        rows += [{"vertical": vertical, "label": "out", "query": q, "top": s, "collection": c} for q, s, c in v_out]

    exit_code = report(in_rows, out_rows)

    adopt, sufficient = _thresholds()
    summary = analyze(rows, adopt, sufficient)
    _print_summary(summary, adopt, sufficient)

    path = args.out or ROOT / "logs" / "rag_scores" / f"rag_scores_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "vertical": args.vertical,
        "thresholds": {"reasoning_min_rag_score": adopt, "rag_sufficient_score": sufficient},
        "summary": summary,
        "results": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n結果: {path}")
    return exit_code


def _thresholds() -> Tuple[float, float]:
    """今の設定のしきい値（採用の下限, Web 検索の要否）。"""
    from grace.config import get_config

    cfg = get_config()
    return float(cfg.executor.reasoning_min_rag_score), float(cfg.qdrant.rag_sufficient_score)


if __name__ == "__main__":
    raise SystemExit(main())
