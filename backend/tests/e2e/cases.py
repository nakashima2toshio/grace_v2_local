"""E2E のケース。**画面の例文ボタンをそのまま使う**（ここに文面を書き写さない）。

例文は「押せば期待どおりの結果が出る」ことに意味があるもの
（`frontend/src/components/ReviewForm.tsx` の `EXAMPLES` の注記）なので、E2E でも
同じ文面を実データ・実 LLM に流す。文面は TSX から読み取る。

期待値（`SUPPORT_EXPECT` / `REVIEW_EXPECT`）だけをここに持つ。例文の追加・改名で
期待値とずれたら `test_e2e_cases.py`（CI で走る）が落ちて知らせる。
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

_FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src" / "components"
QUERY_FORM = _FRONTEND / "QueryForm.tsx"
REVIEW_FORM = _FRONTEND / "ReviewForm.tsx"

_TS_STRING = re.compile(r"'((?:[^'\\\n]|\\.)*)'")


def _decode(literal: str) -> str:
    return literal.replace("\\n", "\n").replace("\\'", "'").replace("\\\\", "\\")


def _block(source: str, start_marker: str) -> str:
    """`const X: 型 = [` の `[` の後ろから対応する `];` までを返す（行コメントは除く）。

    型注釈（`Array<{ label: string; ... }>`）を本体と取り違えないよう、`= [` の後ろから読む。
    """
    start = source.index("= [", source.index(start_marker)) + len("= [")
    end = source.index("\n];", start)
    lines = source[start:end].splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("//"))


def support_examples() -> Dict[str, str]:
    """GRACE-Support タブの例文 → {vertical: query}（業界つきのものだけ）。"""
    block = _block(QUERY_FORM.read_text(encoding="utf-8"), "const VERTICAL_EXAMPLES")
    found: Dict[str, str] = {}
    for query, vertical in re.findall(r"query:\s*'([^']*)',\s*vertical:\s*'(\w+)'", block):
        found[vertical] = query
    return found


def review_examples() -> Dict[str, str]:
    """GRACE-Review タブの例文 → {title: document}。"""
    block = _block(REVIEW_FORM.read_text(encoding="utf-8"), "export const EXAMPLES")
    found: Dict[str, str] = {}
    # 1 例 = `label:` から次の `label:` まで
    for chunk in re.split(r"\blabel:", block)[1:]:
        title = re.search(r"title:\s*'([^']*)'", chunk)
        doc = chunk.split("document:", 1)
        if not title or len(doc) != 2:
            raise ValueError(f"ReviewForm.tsx の EXAMPLES を読み取れません: {chunk[:80]!r}")
        found[title.group(1)] = "".join(_decode(s) for s in _TS_STRING.findall(doc[1]))
    return found


# ----------------------------------------------------------------------
# 期待値
# ----------------------------------------------------------------------
# Support: 業界ごとに「何が起きるべきか」。LLM の言い回しには依存させない。
SUPPORT_EXPECT: Dict[str, str] = {
    # 社内ナレッジ（gov_faq）から回答する。実測 2026-08-21: groundedness 1.00 で回答
    "gov": "answer_from_internal",
    # 「落ち」はエスカレーション語（verticals.py）→ LLM に関係なく強制エスカレ
    "saas": "forced_escalate",
    # 「返品」は action_map → 回答できれば create_ticket、できなければ有人引き継ぎ。
    # どちらでも、判定とアクションが食い違わず、本人確認（ドライラン）を通ること
    "ec": "action_consistent",
}

# Review: 例文のタイトルごとの期待値
REVIEW_EXPECT: Dict[str, Dict[str, object]] = {
    # 優良誤認・薬機法。重大リスク語（No.1 / 治る / 副作用がない）があるので high が出る
    "化粧品LP案": {"min_findings": 1, "min_high": 1},
    # 特商法の表記漏れ。送料の欠落は文字列で決まる事実（review_facts）なので必ず出る
    "表記漏れLP案": {"min_findings": 1, "rule_ids": ["tokusho-01"]},
    # 指摘 0 件を期待する例文（ReviewForm.tsx の注記）。過検知の回帰を捕まえる
    "適正LP案": {"max_findings": 0},
}


def review_rule_ids(findings: List[object]) -> List[str]:
    return [getattr(f, "rule_id", "") for f in findings]


def first_internal_citation(citations: List[str]) -> Optional[str]:
    return next((c for c in citations if c.startswith("[社内]")), None)
