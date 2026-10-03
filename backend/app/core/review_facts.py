# backend/app/core/review_facts.py
"""GRACE-Review の「文字列だけで決まる事実」を調べる純関数群。

LLM の判定は、判定基準に例まで書いてあっても読み落とすことがある。
実測 2026-10-03（grace_v2_local・ローカル LLM・各 2 回）:

- tokusho-01: 「販売価格: 4,980円（税込）」だけで購入時の送料が書かれていない広告を
  2 回とも「違反なし」とした。共通の指示にこの例そのものが書いてある。
- policy-01: 広告「未開封に限り」・規程「未使用・未開封」を 2 回とも「広告が不利」と
  判定した。広告は条件が 1 つ少ない＝顧客に**有利**で、判定基準に「指摘しない」例
  として書いてある組み合わせである。

どちらも、広告文と規程の文字列を見れば機械的に決まる。ここでは LLM に頼らず
その事実だけを取り出し、③ Detect の取りこぼしと ④' の誤検知を止める
（呼び出しは `review_agent._judge` / `_finalize`）。

⚠️ **読み取れないときは何も言わない（None / 判定しない）。** ここは LLM の判断を
上書きする安全網なので、確信が持てる形に当てはまったときだけ働く。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Optional

# 返品・返送について述べている行（購入時の送料ではない）
_RETURN_WORDS = ("返品", "返送", "返却", "交換")
# 購入時の送料の表示とみなす語
_SHIPPING_WORDS = ("送料", "配送料", "配送手数料", "配達料")


def _lines(text: str) -> List[str]:
    return [line.strip() for line in re.split(r"[\n。]", text or "") if line.strip()]


def purchase_shipping_shown(document: str) -> bool:
    """購入時の送料が広告文に書かれているか（「送料無料」「全国一律600円」等を含む）。

    返品・交換の行に出てくる「送料はお客様負担」は**返品時**の送料なので数えない
    （実測 2026-10-03: 表記漏れLP案はこの 1 か所だけに「送料」があった）。
    """
    for line in _lines(document):
        if any(word in line for word in _RETURN_WORDS):
            continue
        if any(word in line for word in _SHIPPING_WORDS):
            return True
    return False


# --- 返品条件 ----------------------------------------------------------------

# 返品を受け付ける条件として使われる語（多いほど顧客に厳しい）
RETURN_CONDITION_WORDS = ("未使用", "未開封", "未着用", "未洗濯", "タグ付き", "箱付き")
_DAYS = re.compile(r"到着(?:後|から)\s*(\d+)\s*日")
_CUSTOMER_PAYS = re.compile(r"(お客様|購入者|お客さま)(?:に|の)?(?:ご)?負担")


@dataclass(frozen=True)
class ReturnTerms:
    """広告または規程から読み取った返品条件。"""

    days: Optional[int]                 # 商品到着後 N 日以内
    conditions: FrozenSet[str]          # 未使用・未開封 など
    customer_pays_shipping: bool        # 返送料を顧客が負担するか


def _return_sentences(texts: Iterable[str]) -> List[str]:
    return [s for text in texts for s in _lines(text) if "返品" in s]


def parse_ad_return_terms(document: str) -> Optional[ReturnTerms]:
    """広告文から返品条件を読み取る。期限が読めなければ None。

    複数の文があるときは、期限は**最も短いもの**、条件と送料負担は**すべての和**を
    取る（広告を顧客に厳しい側へ見積もる）。
    """
    days: List[int] = []
    conditions: set = set()
    customer_pays = False
    for sentence in _return_sentences([document]):
        days += [int(d) for d in _DAYS.findall(sentence)]
        conditions |= {w for w in RETURN_CONDITION_WORDS if w in sentence}
        customer_pays = customer_pays or bool(_CUSTOMER_PAYS.search(sentence))
    if not days:
        return None
    return ReturnTerms(min(days), frozenset(conditions), customer_pays)


def parse_policy_return_terms(policy_texts: Iterable[str]) -> Optional[ReturnTerms]:
    """規程から返品条件を読み取る。期限が読めない・食い違うなら None。

    規程は「顧客に約束している最も手厚い扱い」と比べたいので、広告とは逆に
    **曖昧なら読まない**。期限が 2 種類以上出てきたら判定をあきらめる。
    条件は期限と同じ文に書かれたもの（＝返品を受け付ける条件の本文）だけを使う。

    ⚠️ 返送料は「お客様都合の返品は返送料をお客様にご負担」のように**場合分け**で
    書かれることが多い。ここでは「どこかで顧客負担と書いてあれば顧客負担」と読む
    （不良品の扱いまでは比べない）。画面の OK 例はこの前提で作ってある。
    """
    days: set = set()
    conditions: set = set()
    customer_pays = False
    for sentence in _return_sentences(policy_texts):
        found = [int(d) for d in _DAYS.findall(sentence)]
        if found:
            days |= set(found)
            conditions |= {w for w in RETURN_CONDITION_WORDS if w in sentence}
        customer_pays = customer_pays or bool(_CUSTOMER_PAYS.search(sentence))
    if len(days) != 1:
        return None
    return ReturnTerms(days.pop(), frozenset(conditions), customer_pays)


# policy-01 の指摘が返品以外（交換・解約・価格など）に触れていたら、
# ここでは判定しない（返品条件しか読み取っていないため）。
_OTHER_TOPICS = ("交換", "キャンセル", "解約", "返金", "価格", "料金", "定期")


def return_terms_not_worse(
    finding_message: str, document: str, policy_texts: Iterable[str],
) -> Optional[str]:
    """返品条件について、広告が規程より顧客に不利でないと**言い切れる**なら理由を返す。

    - 指摘文が返品の話でない、または返品以外の話題にも触れている → None（判定しない）
    - 広告・規程のどちらかから期限が読み取れない → None
    - 期限が規程以上、条件が規程の部分集合、返送料の負担が規程より重くない → 理由文

    例（OK 例）: 広告「14日以内・未開封・送料お客様負担」/ 規程「14日以内・未使用・
    未開封・お客様都合は返送料お客様負担」→ 不利ではない。
    """
    message = finding_message or ""
    if "返品" not in message or any(word in message for word in _OTHER_TOPICS):
        return None
    ad = parse_ad_return_terms(document)
    policy = parse_policy_return_terms(policy_texts)
    if ad is None or policy is None:
        return None
    if ad.days < policy.days:
        return None
    if not ad.conditions <= policy.conditions:
        return None
    if ad.customer_pays_shipping and not policy.customer_pays_shipping:
        return None
    ad_conditions = "・".join(sorted(ad.conditions)) or "なし"
    policy_conditions = "・".join(sorted(policy.conditions)) or "なし"
    return (
        "規程と照合した結果、返品条件は顧客に不利ではない"
        f"（期限: 広告 {ad.days} 日 ≥ 規程 {policy.days} 日／"
        f"条件: 広告「{ad_conditions}」は規程「{policy_conditions}」以下）"
    )
