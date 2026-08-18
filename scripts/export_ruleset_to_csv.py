#!/usr/bin/env python3
"""RuleSet の条文情報を Qdrant 登録用 CSV へ書き出す。

## なぜ必要か（実測 2026-08-17 20:07 〜 2026-08-18 21:41）

`ec_ad` の検索スコープは `ec_ad_rules_anthropic` と `ec_policy_anthropic` だが、
**前者は未登録**である。実行ログはこう出る。

    検索スコープ: ec_ad_rules_anthropic, ec_policy_anthropic
    （未登録コレクションは条文フォールバックを使用）
    doc/tokusho-01: 文書全体で判定 / 規程 0 件
    doc/tokusho-02: 文書全体で判定 / 規程 0 件      ← 7 ルール中 6 つが 0 件

結果、指摘の「根拠」はすべて `RuleItem.description`（条文フォールバック）になり、
**「条文つきの指摘を出します」という機能の核が成立していない**。

本スクリプトは `RuleSet` が既に持っている条文情報をそのまま CSV へ落とし、
`qa_qdrant/register_to_qdrant.py` で登録できる形にする。

## ⚠️ これは出発点であって、法務監修済みの条文集ではない

書き出される `answer` は `RuleItem.description`、つまり**このリポジトリが既に
持っている要約**である。`rulesets.py` の冒頭にあるとおり:

    本ルールセットは技術検証用のサンプルであり、法務レビューを受けていない。

したがって**登録しただけでは、根拠の中身は条文フォールバックと同じ**になる。
RAG が意味を持つのは、`answer` を**実際の条文・ガイドラインの本文**へ置き換えて
からである。本スクリプトの役割は次の 2 つに限られる。

1. コレクションを実在させ、`_retrieve_evidence` が実際に規程を引く経路を通す
   （現状は 7 ルール中 6 つが 0 件で、RAG 経路が一度も検証されていない）
2. **列構成が正しい CSV の雛形**を作る（人手で条文本文を埋めるための土台）

## 列構成（`register_to_qdrant.py` の期待に合わせる）

| 列 | 用途 |
|---|---|
| `question` | Embedding 対象（`question + "\\n" + answer` を埋め込む）。**UI の引用ラベル**にもなる |
| `answer` | Embedding 対象。**④ Ground へ渡す根拠本文**になる |
| `topic` | payload の来歴（任意） |

Review 側は `_retrieve_evidence` でこう読む。

    title = payload.get("title") or payload.get("question") or "(規程)"
    body  = payload.get("answer") or payload.get("text") or ""

## 使い方

    # 1. CSV を書き出す
    PYTHONPATH=. python3 scripts/export_ruleset_to_csv.py --ruleset ec_ad

    # 2. Qdrant へ登録する
    python qa_qdrant/register_to_qdrant.py \\
      --input-file qa_output/ec_ad_rules.csv \\
      --collection ec_ad_rules_anthropic \\
      --recreate

⚠️ 本スクリプトは読み取りのみ。Qdrant には触れない。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 既定の出力先。`services.data_pipeline_service.ALLOWED_INPUT_DIRS` に含まれるので、
# CLI からも「データ管理」タブからも登録の入力として選べる。
DEFAULT_OUTPUT = Path("qa_output/ec_ad_rules.csv")

FIELDNAMES = ("question", "answer", "topic")


def build_rows(ruleset) -> List[dict]:
    """`RuleSet` の各ルールを 1 行の dict にする。

    `question` に法令・条・タイトルを置くのは 2 つの理由がある。

    1. **検索が当たるようにするため。** ② Retrieve の検索クエリは
       `f"{rule.title} {rule.description}"`（`review_agent.py`）。`question` に
       タイトル、`answer` に description を置くと、埋め込み対象の
       `question + "\\n" + answer` がクエリとほぼ同じ文になり、
       `RuleSet.evidence_min_score`（既定 0.70）を余裕で超える。
    2. **UI の引用ラベルになるため。** `[規程] 特定商取引法 第11条（販売価格・
       送料の明示）` と表示され、どの条文が根拠かが一目で分かる。
    """
    rows: List[dict] = []
    for rule in ruleset.rules:
        rows.append({
            "question": f"{rule.law} {rule.article}（{rule.title}）",
            "answer": rule.description,
            "topic": rule.category,
        })
    return rows


def write_csv(rows: List[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ruleset", default="ec_ad", help="RuleSet ID（既定: ec_ad）")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"出力先 CSV（既定: {DEFAULT_OUTPUT}）")
    args = parser.parse_args(argv)

    from backend.app.core.rulesets import RULESETS, get_ruleset

    ruleset = get_ruleset(args.ruleset)
    if ruleset is None:
        raise SystemExit(
            f"unknown ruleset: {args.ruleset}（選択肢: {', '.join(RULESETS)}）"
        )

    rows = build_rows(ruleset)
    write_csv(rows, args.output)

    print(f"✅ {len(rows)} 行を書き出しました: {args.output}")
    print()
    print("次の手順で Qdrant へ登録してください:")
    print()
    print("    python qa_qdrant/register_to_qdrant.py \\")
    print(f"      --input-file {args.output} \\")
    print(f"      --collection {args.ruleset}_rules_anthropic \\")
    print("      --recreate")
    print()
    print("⚠️ answer は RuleItem.description（このリポジトリ自身の要約）です。")
    print("   実際の条文・ガイドライン本文へ置き換えるまで、根拠の中身は")
    print("   条文フォールバックと同じままです（法務監修が必要）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
